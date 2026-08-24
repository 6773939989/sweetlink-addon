import os
import logging
from typing import Any, Dict, List, Optional, cast

import requests


# Chiamate all'API REST del Supervisor di Home Assistant.
#
# I permessi non sono uniformi, e la differenza conta:
#
#   - le rotte /addons/self/<azione> passano da una deroga del Supervisor e funzionano con il
#     solo hassio_api, qualunque sia il ruolo dichiarato;
#   - /addons/<slug>/info rientra nel ruolo di base, che ammette tutte le rotte che finiscono
#     per /info: possiamo quindi LEGGERE lo stato di un altro add-on senza privilegi in piu';
#   - scrivere le opzioni di un altro add-on richiede hassio_role: manager. Se il ruolo manca,
#     il Supervisor risponde 403.
#
# Un 403 non e' un errore fatale e non deve fermare l'hub: viene registrato e la chiamata
# restituisce False. Chi chiama decide se puo' proseguire senza.
class SupervisorApi:

    c_BaseUrl = "http://supervisor"
    c_TimeoutSec = 30


    # Vero se giriamo dentro un add-on con accesso al Supervisor.
    @staticmethod
    def IsAvailable() -> bool:
        return len(os.environ.get("SUPERVISOR_TOKEN", "")) > 0


    # Esegue una chiamata e restituisce il campo "data" della risposta, oppure None.
    # None copre sia l'errore di rete sia la risposta negativa: distinguerli non servirebbe a
    # nessuno dei chiamanti, e tutti devono comunque gestire l'assenza del dato.
    @staticmethod
    def _Call(logger:logging.Logger, method:str, path:str, body:Optional[Dict[str, Any]]=None) -> Optional[Dict[str, Any]]:
        token = os.environ.get("SUPERVISOR_TOKEN", "")
        if len(token) == 0:
            logger.warning(f"Supervisor: SUPERVISOR_TOKEN assente, {method} {path} non eseguita.")
            return None
        try:
            response = requests.request(
                method, SupervisorApi.c_BaseUrl + path,
                headers={"Authorization": f"Bearer {token}"},
                json=body, timeout=SupervisorApi.c_TimeoutSec)
        except Exception as e:
            logger.warning(f"Supervisor: {method} {path} fallita: {e}")
            return None

        if response.status_code == 403:
            # Il messaggio nomina il ruolo perche' e' l'unica causa possibile, e senza dirlo
            # chi legge il log non ha modo di capire cosa manca.
            logger.warning(f"Supervisor: {method} {path} negata (403). Serve hassio_role: manager nel config dell'add-on.")
            return None
        if response.status_code < 200 or response.status_code >= 300:
            logger.warning(f"Supervisor: {method} {path} ha risposto HTTP {response.status_code}.")
            return None

        try:
            parsed:Any = response.json()
        except Exception:
            # Alcune rotte rispondono senza corpo: la chiamata e' comunque riuscita.
            return {}
        if not isinstance(parsed, dict):
            return {}
        payload = cast(Dict[str, Any], parsed)
        if payload.get("result") == "error":
            logger.warning(f"Supervisor: {method} {path} ha risposto errore: {payload.get('message')}")
            return None
        data = payload.get("data")
        return data if isinstance(data, dict) else {}


    # Chiede al Supervisor di riavviare questo add-on.
    #
    # Serve dopo aver rigenerato l'identita': plugin_id e private_key vengono letti all'avvio da
    # una decina di componenti che ne tengono una copia, quindi cambiarli sotto i piedi senza
    # ripartire lascerebbe l'hub registrato con l'identita' nuova e operativo con quella vecchia.
    @staticmethod
    def RestartSelf(logger:logging.Logger) -> bool:
        if SupervisorApi._Call(logger, "POST", "/addons/self/restart") is None:
            return False
        logger.info("Riavvio dell'add-on richiesto al Supervisor.")
        return True


    # Chiede al Supervisor di fermare questo add-on.
    #
    # E' il passo finale della preparazione dell'immagine: dopo aver cancellato l'identita' non
    # si deve generare quella nuova, altrimenti l'immagine che si sta per clonare ne conterrebbe
    # una. Fermarsi e' piu' pulito che restare in vita a girare a vuoto, perche' Home Assistant
    # mostra l'add-on come fermo e chi prepara l'immagine vede che il passo e' concluso.
    @staticmethod
    def StopSelf(logger:logging.Logger) -> bool:
        if SupervisorApi._Call(logger, "POST", "/addons/self/stop") is None:
            return False
        logger.info("Arresto dell'add-on richiesto al Supervisor.")
        return True


    # Informazioni su un altro add-on, o None se non e' installato o non e' leggibile.
    # Non richiede privilegi oltre hassio_api: le rotte /info rientrano nel ruolo di base.
    @staticmethod
    def GetAddonInfo(logger:logging.Logger, slug:str) -> Optional[Dict[str, Any]]:
        return SupervisorApi._Call(logger, "GET", f"/addons/{slug}/info")


    # Scrive le opzioni di un altro add-on. RICHIEDE hassio_role: manager.
    # Le opzioni passate vengono fuse con quelle esistenti dal Supervisor, non le sostituiscono.
    @staticmethod
    def SetAddonOptions(logger:logging.Logger, slug:str, options:Dict[str, Any]) -> bool:
        return SupervisorApi._Call(logger, "POST", f"/addons/{slug}/options", {"options": options}) is not None


    # Riavvia un altro add-on. RICHIEDE hassio_role: manager.
    @staticmethod
    def RestartAddon(logger:logging.Logger, slug:str) -> bool:
        return SupervisorApi._Call(logger, "POST", f"/addons/{slug}/restart") is not None


    # Ferma un altro add-on. RICHIEDE hassio_role: manager.
    @staticmethod
    def StopAddon(logger:logging.Logger, slug:str) -> bool:
        return SupervisorApi._Call(logger, "POST", f"/addons/{slug}/stop") is not None


    # Gli slug degli add-on installati il cui nome termina con il suffisso indicato.
    #
    # Lo slug completo di un add-on e' <hash del repository>_<slug>, e l'hash dipende da quale
    # repository lo ha installato: non si puo' scrivere a mano. Per NetBird cerchiamo quindi
    # tutto cio' che finisce per "_netbird", piu' lo slug nudo per il caso di un add-on locale.
    #
    # Usa /addons, che richiede hassio_role: manager. Senza ruolo restituisce lista vuota.
    @staticmethod
    def FindInstalledAddons(logger:logging.Logger, slugSuffix:str) -> List[str]:
        data = SupervisorApi._Call(logger, "GET", "/addons")
        if data is None:
            return []
        addons:Any = data.get("addons")
        if not isinstance(addons, list):
            return []
        found:List[str] = []
        for entry in cast(List[Any], addons):
            if not isinstance(entry, dict):
                continue
            slug = cast(Dict[str, Any], entry).get("slug")
            if not isinstance(slug, str):
                continue
            if slug == slugSuffix or slug.endswith("_" + slugSuffix):
                found.append(slug)
        return sorted(found)
