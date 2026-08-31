# -*- coding: utf-8 -*-
#
# SONDA TEMPORANEA. Va tolta quando la risposta e' stata annotata in
# sweetlink/ASSISTENTI-VOCALI.md, passo 0(a).
#
# LA DOMANDA A CUI RISPONDE, e perche' non si puo' rispondere leggendo il codice.
#
# Il disegno del collegamento vocale poggia tutto su un'ipotesi sola: che l'add-on possa
# chiamare le rotte /api/alexa/smart_home e /api/google_assistant del core presentando il
# SUPERVISOR_TOKEN, cioe' la stessa credenziale che gia' usa per tutto il resto. Se regge,
# il backend inoltra e Home Assistant traduce, e non dobbiamo scrivere nessun traduttore.
#
# Non e' deducibile dal sorgente. Le due viste leggono request["hass_user"].id — per Google
# diventa l'agentUserId, per Alexa il Context.user_id — e cosa il proxy del Supervisor
# consegni a Home Assistant su una POST proxata non sta scritto da nessuna parte che
# abbiamo potuto leggere. O si misura su un apparecchio vero, o si tira a indovinare.
#
# E c'e' una seconda cosa che solo la misura puo' dire: l'agentUserId DEVE restare identico
# fra un riavvio e l'altro. Se cambia, Google considera i dispositivi di un altro utente e
# la casa sparisce dall'app a ogni riavvio dell'hub. Per questo la sonda lo stampa: si
# rilegge dopo un riavvio e si confronta.
import os
import time
import json
import threading
import requests


# Quanto si aspetta prima di provare. UpdateConfigIfNeeded puo' aver appena riavviato Home
# Assistant, e una sonda che parte su un core che si sta ancora alzando misura il riavvio,
# non il permesso.
c_AttesaAvvioSec = 60

# I tentativi, perche' il core puo' metterci piu' del previsto. Un 502 o un rifiuto di
# connessione non e' una risposta: e' Home Assistant che non c'e' ancora.
c_Tentativi = 5
c_PausaFraTentativiSec = 20


def _intestazioni():
    return {
        "Authorization": "Bearer " + os.environ.get("SUPERVISOR_TOKEN", ""),
        "Content-Type": "application/json",
    }


def _chiama(metodo, percorso, corpo=None):
    url = "http://supervisor/core/api" + percorso
    try:
        if metodo == "GET":
            r = requests.get(url, headers=_intestazioni(), timeout=15)
        else:
            r = requests.post(url, headers=_intestazioni(), json=corpo, timeout=15)
        return r.status_code, r.text
    except Exception as e:
        return None, str(e)


def _riassunto(testo, quanto=600):
    if not isinstance(testo, str):
        return ""
    testo = testo.replace(chr(10), " ").replace(chr(13), " ")
    return testo if len(testo) <= quanto else testo[:quanto] + " [...]"


def _sonda(logger):
    time.sleep(c_AttesaAvvioSec)

    for tentativo in range(1, c_Tentativi + 1):
        stato, corpo = _chiama("GET", "/config")
        if stato == 200:
            break
        logger.info(f"[SondaVocale] Home Assistant non ancora pronto ({stato}), tentativo {tentativo}/{c_Tentativi}.")
        if tentativo == c_Tentativi:
            logger.warning("[SondaVocale] Rinuncio: il core non ha risposto. La misura non e' stata fatta.")
            return
        time.sleep(c_PausaFraTentativiSec)

    versione = ""
    try:
        versione = (json.loads(corpo) or {}).get("version", "")
    except Exception:
        pass

    # Google. L'intent SYNC e' quello che elenca i dispositivi: e' anche la risposta che
    # porta l'agentUserId, cioe' il dato da confrontare dopo un riavvio.
    statoG, corpoG = _chiama("POST", "/google_assistant", {
        "requestId": "sonda-passo-zero",
        "inputs": [{"intent": "action.devices.SYNC"}],
    })
    agente = ""
    quantiG = -1
    try:
        rispostaG = json.loads(corpoG) or {}
        carico = rispostaG.get("payload") or {}
        agente = carico.get("agentUserId", "")
        dispositivi = carico.get("devices")
        if isinstance(dispositivi, list):
            quantiG = len(dispositivi)
    except Exception:
        pass

    # Alexa. La direttiva Discovery e' l'equivalente della SYNC.
    statoA, corpoA = _chiama("POST", "/alexa/smart_home", {
        "directive": {
            "header": {
                "namespace": "Alexa.Discovery",
                "name": "Discover",
                "payloadVersion": "3",
                "messageId": "sonda-passo-zero",
            },
            "payload": {"scope": {"type": "BearerToken", "token": "sonda"}},
        }
    })
    quantiA = -1
    try:
        rispostaA = json.loads(corpoA) or {}
        punti = ((rispostaA.get("event") or {}).get("payload") or {}).get("endpoints")
        if isinstance(punti, list):
            quantiA = len(punti)
    except Exception:
        pass

    riga = "=" * 78
    logger.info(riga)
    logger.info("[SondaVocale] PASSO 0(a) — il token del Supervisor sulle rotte vocali")
    logger.info(f"[SondaVocale] Home Assistant versione: {versione}")
    logger.info(f"[SondaVocale] GOOGLE  /api/google_assistant  -> HTTP {statoG}")
    logger.info(f"[SondaVocale]         agentUserId: {agente!r}   (DEVE restare identico dopo un riavvio)")
    logger.info(f"[SondaVocale]         dispositivi elencati: {quantiG}   (-1 = non ho saputo leggerli)")
    logger.info(f"[SondaVocale]         risposta: {_riassunto(corpoG)}")
    logger.info(f"[SondaVocale] ALEXA   /api/alexa/smart_home   -> HTTP {statoA}")
    logger.info(f"[SondaVocale]         endpoint elencati: {quantiA}   (-1 = non ho saputo leggerli)")
    logger.info(f"[SondaVocale]         risposta: {_riassunto(corpoA)}")
    if statoG == 200 and statoA == 200:
        logger.info("[SondaVocale] ESITO: il token basta. L'architettura scelta regge cosi' com'e'.")
    else:
        logger.info("[SondaVocale] ESITO: il token NON basta. Serve il ripiego del bivio B.")
    logger.info(riga)


def Avvia(logger):
    # Sempre in un thread suo e sempre dentro un try: e' una misura, e una misura non deve
    # poter fermare l'add-on. Il thread e' daemon cosi' non trattiene lo spegnimento.
    def _guscio():
        try:
            _sonda(logger)
        except Exception as e:
            logger.warning(f"[SondaVocale] La misura non e' riuscita: {e}")

    threading.Thread(target=_guscio, daemon=True).start()
