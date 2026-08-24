import logging
from typing import Any, Dict, List, Optional, cast


# Chi ha bussato, e se ha il diritto di fare quello che chiede.
#
# Serve per una cosa sola: l'azzeramento pre-clonazione, che distrugge l'identita' dell'hub e non
# si annulla. Il pannello dell'add-on NON e' nella barra laterale di un utente normale, perche'
# panel_admin vale true di default, ma nascosto non vuol dire chiuso: Home Assistant lascia
# apposta la rotta ingress aperta ai non amministratori, e lo scrive nel proprio sorgente
# (components/hassio/websocket_api.py: "Endpoints needed for ingress can't require admin because
# add-ons can set panel_admin: false"). Le rotte /ingress/session e /addons/<slug>/info sono
# raggiungibili da chiunque abbia un account su questo hub, la seconda restituisce l'indirizzo
# ingress con il suo token, e il Supervisor sulla rotta convalida solo che la sessione esista.
#
# Quindi il pulsante non e' l'unica maniglia: la porta e' la rotta HTTP, e va chiusa li'.
class HaAdmin:

    # Identificativo del gruppo amministratori in Home Assistant (auth/const.py: GROUP_ID_ADMIN).
    # Il gruppo degli utenti normali e' "system-users", lo stesso che l'onboarding assegna ai
    # clienti quando crea le loro utenze.
    c_AdminGroupId = "system-admin"

    # Intestazione con l'identificativo dell'utente Home Assistant che ha originato la richiesta.
    #
    # E' affidabile: il Supervisor la scrive da se' a partire dai dati della sessione ingress, e
    # PRIMA cancella qualunque copia arrivata dal client (api/ingress.py, elenco delle
    # intestazioni saltate). Un browser non puo' fabbricarla.
    c_UserIdHeader = "X-Remote-User-Id"


    # Vero se l'utente indicato risulta amministratore nell'elenco fornito da Home Assistant.
    #
    # Restituisce None quando non si puo' rispondere: elenco malformato, oppure utente che in
    # quell'elenco non c'e'. None non e' un "no" motivato ed e' diverso da False, ma chi chiama
    # deve trattarli allo stesso modo: davanti a un'azione irreversibile, non sapere vale no.
    @staticmethod
    def IsAdminInUserList(users:Any, userId:str) -> Optional[bool]:
        if len(userId) == 0 or not isinstance(users, list):
            return None
        for entry in cast(List[Any], users):
            if not isinstance(entry, dict):
                continue
            user = cast(Dict[str, Any], entry)
            if user.get("id") != userId:
                continue
            # Un account disattivato non e' amministratore di niente, qualunque gruppo abbia.
            if user.get("is_active") is False:
                return False
            # Il proprietario dell'installazione lo e' per definizione.
            if user.get("is_owner") is True:
                return True
            groups = user.get("group_ids")
            if not isinstance(groups, list):
                return False
            return HaAdmin.c_AdminGroupId in cast(List[Any], groups)
        return None


    # Chiede a Home Assistant se quell'utente e' amministratore.
    # None se non e' determinabile: connessione assente, risposta mancante o utente sconosciuto.
    @staticmethod
    def IsUserAdmin(logger:logging.Logger, connection:Any, userId:str) -> Optional[bool]:
        if len(userId) == 0 or connection is None:
            return None
        try:
            response = connection.SendMsg({"type": "config/auth/list"}, waitForResponse=True)
        except Exception as e:
            logger.warning(f"Verifica amministratore: la richiesta a Home Assistant e' fallita: {e}")
            return None
        if not isinstance(response, dict):
            return None
        payload = cast(Dict[str, Any], response)
        if payload.get("success") is not True:
            logger.warning(f"Verifica amministratore: Home Assistant ha risposto senza successo: {payload.get('error')}")
            return None
        return HaAdmin.IsAdminInUserList(payload.get("result"), userId)
