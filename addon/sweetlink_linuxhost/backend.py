import os


# L'indirizzo del backend Sweetplace, deciso in un posto solo.
#
# Era scritto a mano in quattro punti fra linuxhost.py e cloud_worker.py, e bastava dimenticarne
# uno perche' meta' dell'add-on parlasse con un server e l'altra meta' con un altro: il reporter
# si registrava da una parte e il worker cloud apriva la socket dall'altra, senza che nessun
# errore lo dicesse. Un cambio di dominio deve toccare una riga, non quattro.
class Backend:

    # Il valore di fabbrica. Cambiarlo qui richiede una nuova versione dell'add-on, ed e' voluto:
    # l'indirizzo del backend e' parte di cosa l'apparecchio E', non una sua preferenza.
    c_DefaultBaseUrl = "https://api.hub-sweetplace.me"

    # Variabile che scavalca il valore di fabbrica. Contiene l'URL COMPLETO di /device/ping e non
    # la sola base, perche' e' cosi' da prima e cambiarne il significato spezzerebbe gli ambienti
    # di prova gia' configurati. Serve a puntare un hub a un backend di collaudo senza
    # ricostruire l'immagine.
    c_OverrideEnvVar = "SWEETPLACE_ONBOARD_API"

    c_DevicePingPath = "/device/ping"


    # L'URL a cui il reporter manda la registrazione dell'hub.
    @staticmethod
    def DevicePingUrl() -> str:
        override = os.environ.get(Backend.c_OverrideEnvVar, "")
        if len(override) > 0:
            return override
        return Backend.c_DefaultBaseUrl + Backend.c_DevicePingPath


    # La radice del backend: ci si appendono il portale di configurazione del cliente e la socket
    # del worker cloud. Si ricava togliendo il percorso dall'URL di registrazione, cosi' resta
    # coerente con l'override anche quando quello punta altrove.
    @staticmethod
    def BaseUrl() -> str:
        return Backend.DevicePingUrl().rsplit("/device", 1)[0]
