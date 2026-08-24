import io
import os
import tempfile
import threading
import logging

import configparser
from typing import List, Optional

# This class is very similar to the config class, but since the config files are often backup
# in public places, the secrets are stored else where.
class Secrets:

    # Note this path and name MUST STAY THE SAME because the installer PY script looks for this file.
    FileName = "sweetlink.secrets"

    # These must stay the same because our installer script requires on the format being as is!
    SecretsSection = "secrets"
    PluginIdKey = "plugin_id"
    PrivateKeyKey = "private_key"
    # Gli indirizzi hardware visti su questo apparecchio. Legano l'identita' qui sopra al ferro
    # su cui e' nata: sono l'unica cosa che non si copia insieme all'immagine della scheda SD.
    BoundMacsKey = "bound_macs"


    # This allows us to add comments into our config.
    # The objects must have two parts, first, a string they target. If the string is found, the comment will be inserted above the target string. This can be a section or value.
    # A string, which is the comment to be inserted.
    c_SecretsConfigComments = [
        { "Target": PluginIdKey,  "Comment": "Uniquely identifies your addon. Don't change or will have to re-link your addon with the service."},
        { "Target": PrivateKeyKey, "Comment": "A private key linked to your addon ID. NEVER share this and also don't change it."},
        { "Target": BoundMacsKey, "Comment": "Hardware addresses this identity belongs to. If none of them is present at boot, the identity was cloned onto another device and gets regenerated."},
    ]


    def __init__(self, logger:logging.Logger, localStoragePath:str) -> None:
        self.Logger = logger

        self.SecretFilePath = os.path.join(localStoragePath, Secrets.FileName)

        # A lock to keep file access super safe
        self.ConfigLock = threading.Lock()
        self.Config:configparser.ConfigParser = None #pyright: ignore[reportAttributeAccessIssue]

        # Load the secret config on init, to ensure it exists.
        # This will throw if there's an error reading the config.
        self._LoadConfigIfNeeded_UnderLock()


    # Returns the plugin id if one exists, otherwise None.
    def GetPluginId(self) -> Optional[str]:
        return self._GetStr(Secrets.SecretsSection, Secrets.PluginIdKey)


    # Sets the plugin id and saves the file.
    def SetPluginId(self, pluginId:Optional[str]) -> None:
        self._SetStr(Secrets.SecretsSection, Secrets.PluginIdKey, pluginId)


    # Returns the private key if one exists, otherwise None.
    def GetPrivateKey(self) -> Optional[str]:
        return self._GetStr(Secrets.SecretsSection, Secrets.PrivateKeyKey)


    # Sets the plugin id and saves the file.
    def SetPrivateKey(self, privateKey:Optional[str]) -> None:
        self._SetStr(Secrets.SecretsSection, Secrets.PrivateKeyKey, privateKey)


    # Restituisce gli indirizzi hardware a cui questa identita' e' legata.
    # Lista vuota se il vincolo non e' mai stato scritto.
    def GetBoundMacs(self) -> List[str]:
        raw = self._GetStr(Secrets.SecretsSection, Secrets.BoundMacsKey)
        if raw is None:
            return []
        return sorted({m.strip().upper() for m in raw.split(",") if len(m.strip()) > 0})


    # Salva gli indirizzi hardware a cui legare questa identita'.
    def SetBoundMacs(self, macs:List[str]) -> None:
        cleaned = sorted({m.strip().upper() for m in macs if len(m.strip()) > 0})
        if len(cleaned) == 0:
            self._SetStr(Secrets.SecretsSection, Secrets.BoundMacsKey, None)
            return
        self._SetStr(Secrets.SecretsSection, Secrets.BoundMacsKey, ",".join(cleaned))


    # Dimentica quello che tiene in memoria e rilegge dal disco alla prossima richiesta.
    #
    # Serve dopo l'azzeramento per la clonazione. Il caricamento e' pigro e si ferma alla prima
    # volta (_LoadConfigIfNeeded_UnderLock esce subito se Config non e' None), quindi dopo aver
    # cancellato il file l'oggetto continua a portarsi dietro identita' e chiave: la prima
    # scrittura di un qualsiasi altro valore le riscriverebbe tutte sul disco, resuscitando
    # esattamente cio' che si era appena tolto.
    def Forget(self) -> None:
        with self.ConfigLock:
            self.Config = None #pyright: ignore[reportAttributeAccessIssue]


    # Gets a value from the config given the header and key.
    # If the value doesn't exist, None is returned.
    def _GetStr(self, section:str, key:str) -> Optional[str]:
        with self.ConfigLock:
            # Ensure we have the config.
            self._LoadConfigIfNeeded_UnderLock()
            # Check if the section and key exists
            if self.Config.has_section(section):
                if key in self.Config[section].keys():
                    return self.Config[section][key]
        return None


    # Sets the value into the config and saves it.
    def _SetStr(self, section:str, key:str, value:Optional[str]=None) -> None:
        # Ensure the value is a string.
        if value is not None:
            value = str(value)
        with self.ConfigLock:
            self._LoadConfigIfNeeded_UnderLock()
            # Ensure the section exists
            if self.Config.has_section(section) is False:
                self.Config.add_section(section)
            if value is None:
                # If we are setting to None, delete the key if it exists.
                if key in self.Config[section].keys():
                    del self.Config[section][key]
            else:
                # If not none, set the key
                self.Config[section][key] = value
            self._SaveConfig_UnderLock()


    def _LoadConfigIfNeeded_UnderLock(self, forceRead=False) -> None:
        if self.Config is not None and forceRead is False:
            return

        # Always create a new object.
        # For our config, we use strict and such, so we know the config is valid.
        self.Config = configparser.ConfigParser()

        # If a config exists, read it.
        # This will throw on failure.
        if os.path.exists(self.SecretFilePath):
            self.Config.read(self.SecretFilePath)
        else:
            # If no config exists, create a new file by writing the empty config now.
            print("Secrets file doesn't exist. Creating a new file now!")
            self._SaveConfig_UnderLock()


    def _SaveConfig_UnderLock(self) -> None:
        if self.Config is None:
            return

        # Il contenuto si costruisce tutto in memoria, e solo alla fine tocca il disco.
        # Questo file e' l'unica copia dell'identita' dell'hub: la versione precedente lo
        # troncava due volte per ogni valore scritto, e un arresto in una di quelle due
        # finestre lasciava sulla scheda un file vuoto o a meta', cioe' un hub che al
        # riavvio non sa piu' chi e'.
        buffer = io.StringIO()
        self.Config.write(buffer)

        # Reinserisce i commenti sopra le chiavi che ne hanno uno. Si itera su uno StringIO
        # invece che su splitlines() perche' spezza le righe solo sul carattere di a capo:
        # splitlines() spezzerebbe anche su \x0b, \x0c, \x85 e i separatori unicode,
        # e un valore che ne contenesse uno finirebbe diviso in due righe di configurazione.
        finalOutput = ""
        for line in io.StringIO(buffer.getvalue()):
            lineLower = line.lower()
            # If anything in the line matches the target, add the comment just before this line.
            for cObj in Secrets.c_SecretsConfigComments:
                if cObj["Target"] in lineLower:
                    # Add the comment.
                    finalOutput += "# " + cObj["Comment"] + os.linesep
                    break
            finalOutput += line

        self._WriteAtomically_UnderLock(finalOutput)


    # Sostituisce il file dei segreti in un colpo solo.
    # Scrive un file temporaneo accanto a quello vero, lo forza sul disco, e solo allora lo
    # rinomina sopra l'originale: os.replace e' atomica, quindi chi legge trova sempre o la
    # versione precedente per intero o quella nuova per intero, mai una via di mezzo.
    def _WriteAtomically_UnderLock(self, contents:str) -> None:
        # Il temporaneo ha un nome unico invece di un ".tmp" fisso. Il lock e' per istanza, non
        # per file, quindi due oggetti Secrets sullo stesso percorso non si escludono a vicenda:
        # con un nome condiviso potrebbero scriverci sopra a vicenda e far arrivare sul file
        # vero un ibrido delle due versioni, cioe' proprio il danno che questa funzione evita.
        # mkstemp lo crea anche a 0600, e os.replace porta quei permessi sul file finale: il
        # file dei segreti smette di essere leggibile da chiunque, come avrebbe sempre dovuto.
        directory = os.path.dirname(self.SecretFilePath) or "."
        fd, tempFilePath = tempfile.mkstemp(dir=directory, prefix=Secrets.FileName + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, 'w', encoding="utf-8") as f:
                f.write(contents)
                # Senza queste due righe il rename puo' raggiungere il disco prima dei dati:
                # dopo un'interruzione di corrente il file esisterebbe, ma vuoto.
                f.flush()
                os.fsync(f.fileno())
            os.replace(tempFilePath, self.SecretFilePath)
        except Exception:
            # Il file precedente e' rimasto intatto, quindi si butta via solo lo scarto.
            try:
                if os.path.exists(tempFilePath):
                    os.remove(tempFilePath)
            except Exception:
                pass
            raise

        # Rende durevole anche la voce di directory creata dal rename. Su Windows aprire una
        # directory fallisce sempre, e li' non c'e' nessun hub da proteggere; su Linux invece
        # un fallimento significa che il rename potrebbe non sopravvivere a un blackout, e
        # tacerlo vorrebbe dire dichiarare una garanzia che non c'e'.
        try:
            dirFd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(dirFd)
            finally:
                os.close(dirFd)
        except OSError as e:
            if os.name != "nt":
                self.Logger.warning(f"fsync della cartella dei segreti fallito, il salvataggio potrebbe non sopravvivere a un'interruzione: {e}")
