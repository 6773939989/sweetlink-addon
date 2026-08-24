import os
import shutil
import logging
from typing import Dict, List, Optional

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
#   - la chiave privata WireGuard di NetBird, nel suo config.json.
#
# La seconda non e' un problema nostro ma ce la portiamo in casa lo stesso: e' un difetto noto
# di NetBird (netbirdio/netbird#1798), e clonare un'immagine con NetBird gia' registrato produce
# una flotta di peer che condividono la stessa chiave e si contendono lo stesso indirizzo.
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


    # Il referto: cosa c'e' ancora sul disco che non deve finire dentro l'immagine clonata.
    # Non modifica niente, si puo' chiamare a ogni disegno del pannello.
    @staticmethod
    def BuildReport(logger:logging.Logger, secrets:Secrets, hardwareMacs:List[str]) -> List[Dict[str, str]]:
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
                "Identificativo di Home Assistant",
                "Presente e verra' clonato: per le statistiche di Home Assistant tutti gli hub risulteranno la stessa istanza. Non influisce su Sweetplace."))

        return report


    @staticmethod
    def _NetbirdFindings(logger:logging.Logger, hardwareMacs:List[str]) -> List[Dict[str, str]]:
        findings:List[Dict[str, str]] = []

        # Se la cartella non e' montata non sappiamo niente, e va detto come blocco e non come
        # avviso: un avviso non ferma nessuno, e chi clona senza sapere si porta dietro la
        # chiave WireGuard del master su tutta la flotta.
        if Netbird.ConfigsRoot() is None:
            findings.append(ImagePrep._Finding(ImagePrep.c_LevelBlock, "NetBird",
                "Non posso controllare la sua chiave privata: la cartella degli add-on non risulta montata. "
                "Verifica che il config.yaml dichiari all_addon_configs, oppure controlla a mano prima di clonare."))
            return findings

        configs = Netbird.FindConfigs()
        if len(configs) == 0:
            findings.append(ImagePrep._Finding(ImagePrep.c_LevelOk,
                "NetBird", "Nessuna configurazione sul disco: ogni hub si registrera' con una chiave propria."))
            return findings

        for path in configs:
            if Netbird.ConfigHasKey(path):
                findings.append(ImagePrep._Finding(ImagePrep.c_LevelBlock,
                    "NetBird", f"Chiave privata presente in {path}. Clonando adesso, i peer si contenderebbero lo stesso indirizzo."))
            else:
                findings.append(ImagePrep._Finding(ImagePrep.c_LevelOk,
                    "NetBird", f"Configurazione presente ma senza chiave: {path}."))

        # Il nome del peer: non blocca la clonazione, ed e' solo una segnalazione. Vedi la nota
        # in fondo a netbird.py per il motivo per cui non lo impostiamo da soli.
        desired = Netbird.PeerNameFromMac(hardwareMacs[0]) if len(hardwareMacs) > 0 else ""
        if len(desired) > 0:
            for path in configs:
                slug = Netbird.SlugFromConfigPath(path)
                current = Netbird.ReadConfiguredHostname(logger, slug)
                if current == desired:
                    findings.append(ImagePrep._Finding(ImagePrep.c_LevelOk,
                        "Nome del peer NetBird", f"{slug} e' configurato come {desired}."))
                else:
                    findings.append(ImagePrep._Finding(ImagePrep.c_LevelWarn,
                        "Nome del peer NetBird",
                        f"{slug} e' configurato come '{current}' invece di {desired}: nella dashboard sara' difficile riconoscerlo. NetBird prende il nome solo alla prima registrazione."))
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
    def Wipe(logger:logging.Logger, secrets:Secrets, storageDir:str) -> List[Dict[str, str]]:
        actions:List[Dict[str, str]] = []

        # 1. NetBird: prima fermarlo, poi cancellare, poi rileggere.
        if Netbird.ConfigsRoot() is None:
            # Nessun accesso alla cartella: non abbiamo toccato niente e non sappiamo niente.
            # Dirlo qui e' obbligatorio, perche' l'alternativa e' un elenco di azioni che tace
            # su NetBird e che chi legge interpreta come "pulito".
            actions.append(ImagePrep._Finding(ImagePrep.c_LevelBlock, "NetBird",
                "Non ho potuto controllare ne' cancellare la sua chiave privata: la cartella degli add-on non risulta montata. "
                "Se NetBird e' installato, la sua chiave e' ancora sul disco. NON clonare prima di aver verificato a mano."))
        else:
            configs = Netbird.FindConfigs()
            if len(configs) == 0:
                actions.append(ImagePrep._Finding(ImagePrep.c_LevelOk, "NetBird",
                    "Nessuna configurazione sul disco, niente da cancellare."))
            else:
                for path in configs:
                    slug = Netbird.SlugFromConfigPath(path)
                    if SupervisorApi.StopAddon(logger, slug):
                        actions.append(ImagePrep._Finding(ImagePrep.c_LevelOk, "NetBird", f"Add-on {slug} fermato."))
                    else:
                        actions.append(ImagePrep._Finding(ImagePrep.c_LevelWarn, "NetBird",
                            f"Non sono riuscito a fermare {slug}: potrebbe riscrivere la chiave subito dopo. Fermalo a mano e riazzera."))

                for path in configs:
                    try:
                        os.remove(path)
                    except Exception as e:
                        actions.append(ImagePrep._Finding(ImagePrep.c_LevelBlock, "NetBird", f"Cancellazione di {path} fallita: {e}"))

                # Rilegge dal disco: se NetBird era ancora vivo puo' aver gia' riscritto il
                # file, e dirlo adesso e' l'unica cosa che impedisce di clonare con la chiave.
                remaining = [p for p in Netbird.FindConfigs() if Netbird.ConfigHasKey(p)]
                if len(remaining) == 0:
                    actions.append(ImagePrep._Finding(ImagePrep.c_LevelOk, "NetBird",
                        f"{len(configs)} configurazioni rimosse, nessuna chiave privata sul disco."))
                else:
                    actions.append(ImagePrep._Finding(ImagePrep.c_LevelBlock, "NetBird",
                        f"La chiave privata e' ancora in {', '.join(remaining)}. NON clonare: ferma NetBird e riprova."))

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

        for a in actions:
            logger.info(f"[Preparazione immagine] {a['level'].upper()} {a['title']}: {a['detail']}")
        return actions
