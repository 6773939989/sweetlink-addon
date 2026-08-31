# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 M2R S.r.l.
# Parte di Sweetlink. Vedi NOTICE.md.

import os
import re
import json
import glob
import logging
from typing import Any, Dict, List, Optional, cast

from .supervisorapi import SupervisorApi


# Tutto cio' che sappiamo dell'add-on NetBird, in un posto solo.
#
# NetBird ci serve come rete di servizio fra gli hub e i nostri servizi in cloud. Non sostituisce
# il tunnel Cloudflare, che resta la via con cui il CLIENTE raggiunge il proprio impianto da un
# browser qualunque; NetBird e' il piano di gestione, e ci arriva solo chi e' gia' nella rete.
class Netbird:

    # Lo slug completo di un add-on e' <hash del repository>_<slug>, e l'hash dipende da quale
    # repository lo ha installato: il nome esatto non si puo' scrivere a mano, si cerca.
    c_Slug = "netbird"
    c_ConfigName = "config.json"

    # Dove Home Assistant monta le cartelle di configurazione di tutti gli add-on installati,
    # quando il nostro config.yaml dichiara "all_addon_configs".
    #
    # Il percorso NON e' "/all_addon_configs": il Supervisor monta su "/addon_configs"
    # (docker/const.py, PATH_ALL_ADDON_CONFIGS). La frase dei documenti secondo cui "tutti i
    # percorsi mappano su /<nome-del-tipo>" non vale per questo tipo, ne' per
    # homeassistant_config che monta su "/homeassistant".
    #
    # Il secondo percorso e' la denominazione nuova: il Supervisor sta rinominando "addon" in
    # "app" e ha gia' la costante PATH_ALL_APP_CONFIGS = "/app_configs". Provarli entrambi costa
    # una riga e evita che questa funzione diventi muta il giorno della migrazione.
    c_ConfigsRootCandidates = ("/addon_configs", "/app_configs")


    # La cartella con le configurazioni degli altri add-on, o None se non e' montata.
    # None non e' un dettaglio: significa che di NetBird non sappiamo NIENTE, e chi chiama deve
    # dirlo invece di comportarsi come se non ci fosse.
    @staticmethod
    def ConfigsRoot() -> Optional[str]:
        for candidate in Netbird.c_ConfigsRootCandidates:
            if os.path.isdir(candidate):
                return candidate
        return None


    # Prefisso del nome con cui l'hub compare nella dashboard NetBird.
    c_PeerNamePrefix = "sweetplace-"

    # Il nome del peer e' validato dall'add-on NetBird con questa espressione, che e' quella di
    # un nome di dominio: lettere, cifre e trattini, mai un trattino in testa o in coda.
    #
    # Da qui il trattino invece del trattino basso: "sweetplace_DCA632AABBCC" verrebbe RIFIUTATO
    # dallo schema dell'add-on. Non e' un capriccio del validatore, e' che quel nome finisce
    # anche nel DNS interno di NetBird, dove il trattino basso non e' un carattere ammesso.
    c_PeerNameRegex = re.compile(r"^([a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*)?$")


    # Il nome con cui questo hub dovrebbe comparire nella dashboard, a partire dal suo indirizzo
    # hardware. Stringa vuota se il MAC non e' utilizzabile.
    @staticmethod
    def PeerNameFromMac(mac:str) -> str:
        cleaned = re.sub(r"[^0-9A-Za-z]", "", mac).upper()
        if len(cleaned) == 0:
            return ""
        name = Netbird.c_PeerNamePrefix + cleaned
        if Netbird.c_PeerNameRegex.match(name) is None:
            return ""
        return name


    # I percorsi dei config.json di NetBird presenti sul disco, uno per ogni add-on NetBird
    # installato. Lista vuota se non ce n'e' nessuno o se la cartella non e' montata: chi chiama
    # deve distinguere i due casi guardando ConfigsRoot().
    @staticmethod
    def FindConfigs() -> List[str]:
        root = Netbird.ConfigsRoot()
        if root is None:
            return []
        try:
            found:List[str] = []
            # Due forme e non una sola con la stella davanti: "*netbird" prenderebbe anche una
            # cartella chiamata "qualcosamionetbird", e qui dentro si cancella.
            for folder in ("*_" + Netbird.c_Slug, Netbird.c_Slug):
                pattern = os.path.join(root, folder, Netbird.c_ConfigName)
                found.extend(p for p in glob.glob(pattern) if os.path.isfile(p))
            return sorted(set(found))
        except Exception:
            return []


    # Lo slug dell'add-on a cui appartiene un config.json, cioe' il nome della sua cartella.
    # Si ricava dal disco e non dall'elenco del Supervisor, che richiederebbe hassio_role.
    @staticmethod
    def SlugFromConfigPath(path:str) -> str:
        return os.path.basename(os.path.dirname(path))


    # Vero se il file contiene una chiave privata valorizzata, cioe' se quel peer e' gia'
    # registrato. Un file illeggibile lo trattiamo come registrato: nel dubbio si cancella,
    # perche' l'errore in un verso costa una re-registrazione e nell'altro una flotta di cloni
    # che si contendono lo stesso indirizzo.
    @staticmethod
    def ConfigHasKey(path:str) -> bool:
        try:
            with open(path, "r", encoding="utf-8") as f:
                parsed:Any = json.load(f)
            if not isinstance(parsed, dict):
                return True
            key = cast(Dict[str, Any], parsed).get("PrivateKey")
            return isinstance(key, str) and len(key) > 0
        except Exception:
            return True


    # Il nome con cui l'add-on NetBird e' configurato adesso, o None se non e' leggibile.
    # Non richiede privilegi oltre hassio_api: le rotte /info rientrano nel ruolo di base.
    @staticmethod
    def ReadConfiguredHostname(logger:logging.Logger, slug:str) -> Any:
        info = SupervisorApi.GetAddonInfo(logger, slug)
        if info is None:
            return None
        options:Any = info.get("options")
        if not isinstance(options, dict):
            return None
        return cast(Dict[str, Any], options).get("hostname")


    # NOTA SUL NOME DEL PEER, e perche' qui non c'e' nessuna funzione che lo imposta.
    #
    # Sembra ovvio: si scrive l'opzione "hostname" dell'add-on NetBird e l'hub compare nella
    # dashboard con il proprio nome. Non funziona, per due ragioni che si sommano.
    #
    # 1. NetBird assegna il nome del peer SOLO alla registrazione (management/server/peer.go:797,
    #    "Name: peer.Meta.Hostname", dentro l'aggiunta del peer). Al login di un peer gia'
    #    registrato aggiorna solo la SSHKey: il nome, una volta preso, non cambia piu' se non
    #    dalla dashboard o dall'API di gestione.
    # 2. L'add-on NetBird dichiara "startup: services", il nostro non dichiara niente e quindi
    #    vale il default "application", che parte DOPO. Su un hub appena clonato NetBird si
    #    registra sempre prima che noi possiamo dirgli come chiamarsi.
    #
    # Il risultato e' che scrivere l'opzione non rinomina niente, e riavviare NetBird per
    # fargliela leggere butta giu' la rete privata senza ottenere nulla in cambio. L'unica via
    # che funzionerebbe e' forzare una registrazione nuova (cancellare il suo config.json e
    # riavviarlo), che pero' lascia nella dashboard un peer orfano con il nome sbagliato.
    #
    # Per ora il nome lo segnaliamo soltanto, nel referto di preparazione dell'immagine: chi
    # prepara vede che non corrisponde e decide. La scelta e' aperta nel brainstorm.
