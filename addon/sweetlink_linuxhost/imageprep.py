# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 M2R S.r.l.
# Parte di Sweetlink. Vedi NOTICE.md.

import os
import shutil
import logging
from typing import Any, Dict, List, Optional, cast

from .secrets import Secrets
from .netbird import Netbird
from .supervisorapi import SupervisorApi


# Preparazione dell'immagine base: il momento in cui si azzera l'apparecchio "master" prima di
# clonarne il disco su tutti gli hub di produzione.
#
# E' il passaggio piu' pericoloso dell'intero processo, perche' tutto cio' che resta sul disco
# viene duplicato su ogni hub, e un'identita' duplicata non si manifesta subito: si manifesta
# settimane dopo, quando gli apparecchi sono gia' dai clienti. Le identita' da azzerare non sono
# una sola:
#
#   - plugin_id e private_key di Sweetlink, in /data/sweetlink.secrets;
#   - la chiave privata WireGuard di NetBird, nel suo config.json;
#   - gli account di Home Assistant, con le loro password e le loro sessioni gia' attive.
#
# La seconda non e' un problema nostro ma ce la portiamo in casa lo stesso: e' un difetto noto
# di NetBird (netbirdio/netbird#1798), e clonare un'immagine con NetBird gia' registrato produce
# una flotta di peer che condividono la stessa chiave e si contendono lo stesso indirizzo.
#
# La terza e' la piu' insidiosa, perche' non e' "nostra" e non sta in nessun file che tocchiamo:
# vive nello .storage di Home Assistant, che si clona insieme al resto del disco. Un'immagine
# preparata su un apparecchio gia' configurato consegna a ogni cliente l'account di chi l'ha
# preparata, e chi ha quell'immagine, o solo quelle credenziali, entra in QUALUNQUE hub costruito
# a partire da essa. I telefoni gia' accoppiati continuano a funzionare, perche' il loro refresh
# token e il loro webhook stanno nello stesso .storage.
#
# REGOLA DI FONDO: non dichiarare mai pulito cio' che non si e' potuto guardare. Un referto che
# tace su NetBird perche' la cartella non e' montata e' peggio di nessun referto, perche' chi
# legge lo interpreta come "a posto" e clona.
class ImagePrep:

    # Livelli del referto.
    c_LevelOk = "ok"          # a posto
    c_LevelWarn = "warn"      # da sapere, non impedisce la clonazione
    c_LevelBlock = "block"    # clonare adesso produce hub guasti

    # Parola che l'operatore deve digitare per confermare. Il pulsante da solo non basta:
    # questa azione distrugge l'identita' dell'apparecchio e non si annulla.
    c_ConfirmWord = "AZZERA"


    # La cartella di configurazione di Home Assistant, o None se non e' determinabile.
    # Il Supervisor monta homeassistant_config su "/homeassistant"; "/config" e' la forma
    # storica. Si riconosce dal contenuto invece di fidarsi di un percorso fisso.
    @staticmethod
    def FindHaConfigDir() -> Optional[str]:
        for candidate in ("/homeassistant", "/config", "/homeassistant_config"):
            if os.path.isdir(os.path.join(candidate, ".storage")):
                return candidate
        return None


    @staticmethod
    def _Finding(level:str, title:str, detail:str) -> Dict[str, str]:
        return {"level": level, "title": title, "detail": detail}


    # Gli account di Home Assistant presenti su questo disco.
    #
    # Si chiedono a Home Assistant con config/auth/list invece di leggere .storage/auth, per due
    # ragioni. La prima e' che il formato di quel file e' un dettaglio interno di Home Assistant:
    # un controllo che lo interpreta a naso smette di funzionare a un aggiornamento senza dare
    # errore, e un controllo che fallisce in silenzio produce un referto che dice "pulito".
    # La seconda e' che quella chiamata e' gia' quella che usiamo altrove (haadmin.py:67,
    # cloud_worker.py:280), quindi non aggiunge una dipendenza nuova su cui sbagliare.
    #
    # IL LIVELLO DELL'ESITO NEGATIVO LO DECIDE CHI CHIAMA, E NON E' UN CAPRICCIO.
    # Nel referto e' bloccante: c'e' qualcosa su questo disco che non deve essere clonato, e il
    # riepilogo deve dirlo in rosso.
    # Nell'azzeramento e' un avviso, perche' li' un livello bloccante tiene acceso l'add-on
    # (linuxhost.py:145) in attesa che l'operatore "risolva e riazzeri", e questa cosa, in quel
    # momento, non e' risolvibile: l'operatore sta guardando il pannello attraverso ingress, cioe'
    # autenticato con uno degli account che il referto gli chiede di togliere. Un blocco che non
    # si puo' sbloccare non e' una protezione, e' una procedura che non finisce.
    @staticmethod
    def _HaAccountFindings(logger:logging.Logger, haConnection:Any, livelloGrave:str) -> List[Dict[str, str]]:
        titolo = "Account del sistema operativo"

        if haConnection is None:
            return [ImagePrep._Finding(livelloGrave, titolo,
                "Non ho potuto controllarli: manca la connessione a Home Assistant. Se su questo apparecchio "
                "esiste anche un solo account, clonare adesso lo consegna a tutta la flotta. Riavvia l'add-on e "
                "rileggi il referto: non clonare prima di aver visto questa riga verde.")]

        try:
            # Timeout corto: il referto si ricostruisce a ogni disegno del pannello, e un Home
            # Assistant lento non deve tenere appesa la pagina che serve a capire cosa succede.
            response = haConnection.SendMsg({"type": "config/auth/list"}, waitForResponse=True, timeout=5.0)
        except Exception as e:
            logger.warning(f"Referto: config/auth/list e' fallita: {e}")
            response = None

        if not isinstance(response, dict) or cast(Dict[str, Any], response).get("success") is not True:
            return [ImagePrep._Finding(livelloGrave, titolo,
                "Home Assistant non ha risposto all'elenco degli account, quindi non so quanti ce ne siano. "
                "Guardali a mano prima di clonare: un'immagine pronta non ne ha nessuno.")]

        risultato = cast(Dict[str, Any], response).get("result")
        if not isinstance(risultato, list):
            return [ImagePrep._Finding(livelloGrave, titolo,
                "Home Assistant ha risposto in un formato che non riconosco: non so quanti account ci siano. "
                "Guardali a mano prima di clonare: un'immagine pronta non ne ha nessuno.")]

        voci = cast(List[Any], risultato)

        # system_generated distingue gli account di servizio (Supervisor, add-on) da quelli delle
        # persone. Se il campo non c'e' NON si tira a indovinare: si dichiara di non aver potuto
        # distinguere. E' la regola di fondo di questo modulo applicata al caso in cui e' Home
        # Assistant a cambiare sotto di noi.
        campoAssente = False
        diServizio = 0
        persone:List[str] = []
        for voce in voci:
            if not isinstance(voce, dict):
                continue
            utente = cast(Dict[str, Any], voce)
            generato = utente.get("system_generated")
            if generato is None:
                campoAssente = True
            if generato is True:
                diServizio += 1
                continue
            nome = utente.get("name")
            persone.append(str(nome) if nome else "senza nome")

        if campoAssente:
            return [ImagePrep._Finding(livelloGrave, titolo,
                f"Home Assistant non dice quali dei {len(voci)} account sono di servizio, quindi non posso "
                "distinguerli da quelli delle persone. Guardali a mano prima di clonare.")]

        if len(persone) == 0:
            return [ImagePrep._Finding(ImagePrep.c_LevelOk, titolo,
                f"Nessun account di persona: solo {diServizio} di servizio, che ogni hub rigenera per conto suo.")]

        elenco = ", ".join(persone[:4]) + (" e altri" if len(persone) > 4 else "")
        return [ImagePrep._Finding(livelloGrave, titolo,
            f"Ce ne sono {len(persone)} ({elenco}), e non li cancello io. Vengono clonati con il disco: ogni hub "
            "nascerebbe con queste persone, le loro password e i telefoni gia' accoppiati, e chi ha l'immagine "
            "entrerebbe in tutti gli hub costruiti da essa. Un'immagine pronta non ha account di persone: al "
            "primo avvio il sistema operativo deve mostrare la propria configurazione iniziale. Non e' una cosa "
            "che questo add-on possa fare da se', perche' tu stai usando uno di quegli account proprio adesso "
            "per leggere questa pagina.")]


    # Cosa altro, nella cartella di Home Assistant, finisce dentro l'immagine.
    #
    # Qui si guarda il disco e si riferisce quello che c'e', invece di cercare un elenco di nomi
    # attesi: un nome sbagliato, o cambiato da Home Assistant, darebbe un controllo che non trova
    # niente e un referto che dice "pulito". Meglio "ho trovato questo" che "non ho trovato
    # quello che mi aspettavo".
    @staticmethod
    def _HaDiskFindings(haDir:str) -> List[Dict[str, str]]:
        findings:List[Dict[str, str]] = []
        try:
            nomi = os.listdir(haDir)
        except Exception as e:
            return [ImagePrep._Finding(ImagePrep.c_LevelBlock, "Cartella del sistema operativo",
                f"Non riesco a leggerla ({e}), quindi non so cosa contenga. Guardala a mano prima di clonare.")]

        cronologia = [n for n in nomi if ".db" in n.lower()]
        if len(cronologia) > 0:
            findings.append(ImagePrep._Finding(ImagePrep.c_LevelWarn, "Cronologia",
                f"{len(cronologia)} file di database sul disco ({cronologia[0]} e simili): ogni hub partirebbe "
                "con la cronologia dell'apparecchio usato per preparare l'immagine."))

        # Si guarda il CONTENUTO e non l'esistenza, per lo stesso motivo di
        # _SecretsFileHasIdentity: Home Assistant crea un secrets.yaml di soli commenti, e
        # bloccare su un file vuoto vorrebbe dire mostrare una riga rossa che non si puo'
        # togliere. Una riga rossa permanente e' una riga che si smette di leggere.
        segreti = [n for n in nomi if "secret" in n.lower()]
        pieni:List[str] = []
        illeggibili:List[str] = []
        for n in segreti:
            percorso = os.path.join(haDir, n)
            if not os.path.isfile(percorso):
                continue
            try:
                with open(percorso, "r", encoding="utf-8", errors="replace") as f:
                    if any(r.strip() and not r.lstrip().startswith("#") for r in f):
                        pieni.append(n)
            except Exception:
                illeggibili.append(n)

        if len(pieni) > 0:
            findings.append(ImagePrep._Finding(ImagePrep.c_LevelBlock, "File di segreti",
                f"Contengono qualcosa e vengono clonati cosi' come sono: {', '.join(pieni)}. Svuotali o "
                "cancellali se dentro c'e' una chiave ancora valida."))
        if len(illeggibili) > 0:
            findings.append(ImagePrep._Finding(ImagePrep.c_LevelBlock, "File di segreti",
                f"Non riesco a leggerli, quindi non so cosa contengano: {', '.join(illeggibili)}. "
                "Guardali a mano prima di clonare."))

        return findings


    # Il referto: cosa c'e' ancora sul disco che non deve finire dentro l'immagine clonata.
    # Non modifica niente, si puo' chiamare a ogni disegno del pannello.
    @staticmethod
    def BuildReport(logger:logging.Logger, secrets:Secrets, hardwareMacs:List[str], haConnection:Any = None) -> List[Dict[str, str]]:
        report:List[Dict[str, str]] = []

        # 1. L'identita' di Sweetlink.
        try:
            pluginId = secrets.GetPluginId()
            boundMacs = secrets.GetBoundMacs()
        except Exception as e:
            logger.warning(f"Referto: lettura dei segreti fallita: {e}")
            pluginId, boundMacs = None, []
        if pluginId is None:
            report.append(ImagePrep._Finding(ImagePrep.c_LevelOk,
                "Identita' Sweetlink", "Assente: ogni hub ne generera' una propria al primo avvio."))
        else:
            report.append(ImagePrep._Finding(ImagePrep.c_LevelBlock,
                "Identita' Sweetlink", "Presente. Clonando adesso, tutti gli hub nascerebbero con lo stesso plugin_id."))
        if len(boundMacs) > 0:
            report.append(ImagePrep._Finding(ImagePrep.c_LevelBlock,
                "Vincolo hardware", f"Legato a {len(boundMacs)} indirizzi di questo apparecchio."))

        # 2. Gli indirizzi hardware. Senza, l'hub non riesce nemmeno a registrarsi.
        if len(hardwareMacs) == 0:
            report.append(ImagePrep._Finding(ImagePrep.c_LevelBlock,
                "Indirizzi hardware", "Nessun indirizzo leggibile: un hub in queste condizioni non si registra."))
        else:
            report.append(ImagePrep._Finding(ImagePrep.c_LevelOk,
                "Indirizzi hardware", f"{len(hardwareMacs)} leggibili, il primo e' {hardwareMacs[0]}."))

        # 3. NetBird.
        report.extend(ImagePrep._NetbirdFindings(logger, hardwareMacs))

        # 4. L'identificativo dell'istanza Home Assistant. Non e' nostro e non lo tocchiamo, ma
        #    si clona insieme al resto e chi prepara l'immagine deve saperlo.
        haDir = ImagePrep.FindHaConfigDir()
        if haDir is not None and os.path.isfile(os.path.join(haDir, ".storage", "core.uuid")):
            report.append(ImagePrep._Finding(ImagePrep.c_LevelWarn,
                "Identificativo del sistema operativo",
                "Presente e verra' clonato: per le statistiche del sistema operativo tutti gli hub risulteranno la stessa istanza. Non influisce su Sweetplace."))

        # 5. Gli account del sistema operativo, che sono la cosa piu' grave che si possa clonare,
        #    e il resto della sua cartella.
        report.extend(ImagePrep._HaAccountFindings(logger, haConnection, ImagePrep.c_LevelBlock))
        if haDir is not None:
            report.extend(ImagePrep._HaDiskFindings(haDir))

        return report


    @staticmethod
    def _NetbirdFindings(logger:logging.Logger, hardwareMacs:List[str]) -> List[Dict[str, str]]:
        findings:List[Dict[str, str]] = []

        # Se la cartella non e' montata non sappiamo niente, e va detto come blocco e non come
        # avviso: un avviso non ferma nessuno, e chi clona senza sapere si porta dietro la
        # chiave WireGuard del master su tutta la flotta.
        if Netbird.ConfigsRoot() is None:
            findings.append(ImagePrep._Finding(ImagePrep.c_LevelBlock, "Tunnel protetto",
                "Non posso controllare la sua chiave privata: la cartella degli add-on non risulta montata. "
                "Verifica che il config.yaml dichiari all_addon_configs, oppure controlla a mano prima di clonare."))
            return findings

        configs = Netbird.FindConfigs()
        if len(configs) == 0:
            findings.append(ImagePrep._Finding(ImagePrep.c_LevelOk,
                "Tunnel protetto", "Nessuna configurazione sul disco: ogni hub si registrera' con una chiave propria."))
            return findings

        for path in configs:
            if Netbird.ConfigHasKey(path):
                findings.append(ImagePrep._Finding(ImagePrep.c_LevelBlock,
                    "Tunnel protetto", f"Chiave privata presente in {path}. Clonando adesso, i peer si contenderebbero lo stesso indirizzo."))
            else:
                findings.append(ImagePrep._Finding(ImagePrep.c_LevelOk,
                    "Tunnel protetto", f"Configurazione presente ma senza chiave: {path}."))

        # Il nome del peer: non blocca la clonazione, ed e' solo una segnalazione. Vedi la nota
        # in fondo a netbird.py per il motivo per cui non lo impostiamo da soli.
        desired = Netbird.PeerNameFromMac(hardwareMacs[0]) if len(hardwareMacs) > 0 else ""
        if len(desired) > 0:
            for path in configs:
                slug = Netbird.SlugFromConfigPath(path)
                current = Netbird.ReadConfiguredHostname(logger, slug)
                if current == desired:
                    findings.append(ImagePrep._Finding(ImagePrep.c_LevelOk,
                        "Nome nel tunnel protetto", f"{slug} e' configurato come {desired}."))
                else:
                    findings.append(ImagePrep._Finding(ImagePrep.c_LevelWarn,
                        "Nome nel tunnel protetto",
                        f"{slug} e' configurato come '{current}' invece di {desired}: nella dashboard sara' difficile riconoscerlo. Il nome viene assegnato solo alla prima registrazione."))
        return findings


    # Vero se il referto contiene almeno una voce che impedisce la clonazione.
    @staticmethod
    def HasBlockers(report:List[Dict[str, str]]) -> bool:
        return any(f.get("level") == ImagePrep.c_LevelBlock for f in report)


    # Vero se il file dei segreti sul disco contiene ancora un'identita'.
    #
    # Si guarda il contenuto e non l'esistenza del file, perche' la prima lettura dopo
    # l'azzeramento lo ricrea vuoto: un file da zero byte non e' un'identita', e trattarlo come
    # tale renderebbe il referto inutilizzabile subito dopo aver funzionato.
    @staticmethod
    def _SecretsFileHasIdentity(storageDir:str) -> bool:
        path = os.path.join(storageDir, Secrets.FileName)
        if not os.path.isfile(path):
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                contents = f.read()
        except Exception:
            # Illeggibile: nel dubbio si dichiara sporco, mai pulito.
            return True
        for line in contents.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or "=" not in stripped:
                continue
            name, value = stripped.split("=", 1)
            if name.strip() in (Secrets.PluginIdKey, Secrets.PrivateKeyKey, Secrets.BoundMacsKey):
                if len(value.strip()) > 0:
                    return True
        return False


    # Cancella tutto cio' che identifica questo apparecchio, e racconta cosa ha fatto.
    #
    # L'ordine conta. NetBird va fermato PRIMA di cancellargli la configurazione: il demone
    # tiene la chiave in memoria e riscriverebbe il file, rimettendo dentro l'immagine proprio
    # cio' che stiamo togliendo. Se non riusciamo a fermarlo lo diciamo, invece di far credere
    # che sia andata bene.
    @staticmethod
    def Wipe(logger:logging.Logger, secrets:Secrets, storageDir:str, haConnection:Any = None) -> List[Dict[str, str]]:
        actions:List[Dict[str, str]] = []

        # 1. NetBird: prima fermarlo, poi cancellare, poi rileggere.
        if Netbird.ConfigsRoot() is None:
            # Nessun accesso alla cartella: non abbiamo toccato niente e non sappiamo niente.
            # Dirlo qui e' obbligatorio, perche' l'alternativa e' un elenco di azioni che tace
            # su NetBird e che chi legge interpreta come "pulito".
            actions.append(ImagePrep._Finding(ImagePrep.c_LevelBlock, "Tunnel protetto",
                "Non ho potuto controllare ne' cancellare la sua chiave privata: la cartella degli add-on non risulta montata. "
                "Se il tunnel protetto e' installato, la sua chiave e' ancora sul disco. NON clonare prima di aver verificato a mano."))
        else:
            configs = Netbird.FindConfigs()
            if len(configs) == 0:
                actions.append(ImagePrep._Finding(ImagePrep.c_LevelOk, "Tunnel protetto",
                    "Nessuna configurazione sul disco, niente da cancellare."))
            else:
                for path in configs:
                    slug = Netbird.SlugFromConfigPath(path)
                    if SupervisorApi.StopAddon(logger, slug):
                        actions.append(ImagePrep._Finding(ImagePrep.c_LevelOk, "Tunnel protetto", f"Add-on {slug} fermato."))
                    else:
                        actions.append(ImagePrep._Finding(ImagePrep.c_LevelWarn, "Tunnel protetto",
                            f"Non sono riuscito a fermare {slug}: potrebbe riscrivere la chiave subito dopo. Fermalo a mano e riazzera."))

                for path in configs:
                    try:
                        os.remove(path)
                    except Exception as e:
                        actions.append(ImagePrep._Finding(ImagePrep.c_LevelBlock, "Tunnel protetto", f"Cancellazione di {path} fallita: {e}"))

                # Rilegge dal disco: se NetBird era ancora vivo puo' aver gia' riscritto il
                # file, e dirlo adesso e' l'unica cosa che impedisce di clonare con la chiave.
                remaining = [p for p in Netbird.FindConfigs() if Netbird.ConfigHasKey(p)]
                if len(remaining) == 0:
                    actions.append(ImagePrep._Finding(ImagePrep.c_LevelOk, "Tunnel protetto",
                        f"{len(configs)} configurazioni rimosse, nessuna chiave privata sul disco."))
                else:
                    actions.append(ImagePrep._Finding(ImagePrep.c_LevelBlock, "Tunnel protetto",
                        f"La chiave privata e' ancora in {', '.join(remaining)}. NON clonare: ferma il tunnel protetto e riprova."))

        # 2. L'identita' di Sweetlink e tutto il resto di /data, tranne le opzioni dell'add-on,
        #    che sono di Home Assistant e non nostre.
        removed = 0
        try:
            names = os.listdir(storageDir)
        except Exception as e:
            actions.append(ImagePrep._Finding(ImagePrep.c_LevelBlock, "Identita' Sweetlink", f"Cartella dati illeggibile: {e}"))
            names = []
        for filename in names:
            if filename == "options.json":
                continue
            filePath = os.path.join(storageDir, filename)
            try:
                if os.path.isdir(filePath) and not os.path.islink(filePath):
                    shutil.rmtree(filePath)
                else:
                    os.unlink(filePath)
                removed += 1
            except Exception as e:
                actions.append(ImagePrep._Finding(ImagePrep.c_LevelBlock, "Identita' Sweetlink", f"Cancellazione di {filePath} fallita: {e}"))
        actions.append(ImagePrep._Finding(ImagePrep.c_LevelOk, "Identita' Sweetlink", f"{removed} elementi rimossi da {storageDir}."))

        # 3. Butta via la copia in memoria. Il caricamento e' pigro e si ferma alla prima volta,
        #    quindi senza questo passo la prima scrittura di un qualsiasi valore riscriverebbe
        #    sul disco l'identita' appena cancellata.
        try:
            secrets.Forget()
        except Exception as e:
            actions.append(ImagePrep._Finding(ImagePrep.c_LevelBlock, "Identita' Sweetlink",
                f"Non sono riuscito a scartare la copia in memoria dei segreti: {e}. Riavvia l'add-on prima di clonare."))

        # 4. Verifica guardando il disco, non fidandosi delle cancellazioni.
        if ImagePrep._SecretsFileHasIdentity(storageDir):
            actions.append(ImagePrep._Finding(ImagePrep.c_LevelBlock,
                "Verifica", f"{Secrets.FileName} contiene ancora un'identita'. NON clonare."))
        else:
            actions.append(ImagePrep._Finding(ImagePrep.c_LevelOk,
                "Verifica", "Riletto dal disco: nessuna identita' nel file dei segreti."))

        try:
            # I file che altri componenti ricreano da soli (statistiche, cache mDNS) non sono
            # identita' e non impediscono la clonazione: si elencano e basta.
            leftovers = sorted(n for n in os.listdir(storageDir) if n not in ("options.json", Secrets.FileName))
        except Exception:
            leftovers = []
        if len(leftovers) > 0:
            actions.append(ImagePrep._Finding(ImagePrep.c_LevelWarn,
                "Verifica", f"Nella cartella dati sono ricomparsi: {', '.join(leftovers)}. Non sono identita', ma verranno clonati."))

        # 5. GLI ACCOUNT DI HOME ASSISTANT: SI CONTROLLANO, NON SI CANCELLANO.
        #
        # Perche' questo azzeramento NON li cancella, pur essendo la cosa piu' pericolosa che
        # resta sul disco:
        #  - li cancellerebbe mentre l'operatore sta usando proprio uno di quegli account per
        #    guardare questa pagina, attraverso ingress. Si ritroverebbe buttato fuori a meta'
        #    procedura, senza il referto che gli dice se e' andata bene: e il referto e' l'unico
        #    strumento che ha per capirlo;
        #  - Home Assistant tiene lo .storage anche in memoria e lo riscrive per conto suo,
        #    quindi una cancellazione fatta da fuori mentre gira non e' affidabile. Lo stesso
        #    motivo per cui NetBird va fermato prima di toccargli la chiave;
        #  - un hub senza nessun account non e' rotto: al primo avvio Home Assistant mostra la
        #    sua procedura iniziale. Ma e' una decisione che deve prendere chi prepara
        #    l'immagine, guardando l'elenco, non un effetto collaterale di un pulsante.
        #
        # Qui il livello e' AVVISO e non blocco, al contrario del referto. Un blocco fra le azioni
        # tiene acceso l'add-on (linuxhost.py:145) con l'istruzione "risolvi, riazzera, e spegni
        # solo quando e' tutto verde": un'istruzione che in questo caso non si puo' eseguire,
        # perche' l'operatore e' collegato con uno degli account che dovrebbe togliere. Verde non
        # arriverebbe mai, e la procedura non finirebbe.
        # Nel referto, che non comanda niente e si limita a descrivere il disco, resta bloccante.
        actions.extend(ImagePrep._HaAccountFindings(logger, haConnection, ImagePrep.c_LevelWarn))

        for a in actions:
            logger.info(f"[Preparazione immagine] {a['level'].upper()} {a['title']}: {a['detail']}")
        return actions
