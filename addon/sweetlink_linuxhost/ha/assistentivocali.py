# -*- coding: utf-8 -*-
#
# I FILE DI CONFIGURAZIONE DEGLI ASSISTENTI VOCALI, GENERATI DA NOI.
#
# Il configuration.yaml di ogni hub aggancia due integrazioni native a due file che stanno in
# una cartella nostra:
#
#     alexa: !include sweetplace/sweetlink/alexa.yaml
#     google_assistant: !include sweetplace/sweetlink/google_assistant.yaml
#
# Quei due file li scrive questo modulo, leggendo la lista curata che sta altrove
# (vocal_assistants/, mantenuta fuori dall'add-on) e traducendola nella forma che i due
# componenti nativi accettano.
#
# PERCHE' SI GENERA INVECE DI AGGANCIARE DIRETTAMENTE LA LISTA CURATA.
# "smart_home: !include .../alexa.yaml" funziona, ed e' stato provato. Ma se quel file venisse
# svuotato per sbaglio resterebbe un filtro con liste vuote — e un filtro vuoto NON vuol dire
# "non esporre niente": vuol dire "nessun filtro", cioe' esporre tutto. Sta scritto nel sorgente
# (alexa/smart_home.py:89): "if not self._config[CONF_FILTER].empty_filter" salta il ramo del
# filtro e ricade su "espone tutto cio' che non e' ausiliario". Un file svuotato per sbaglio
# aprirebbe la casa intera senza un errore da nessuna parte.
# Generando, la lettura della lista e' nostra: se non riesce lo sappiamo, e chiudiamo.
import io
import os
import tempfile


# LA VERSIONE DELLA RICETTA, cioe' della FORMA con cui scriviamo i due file.
#
# Si alza ogni volta che cambia il modo in cui generiamo, anche se la lista curata e' la stessa.
# Serve al controllo incrociato: un file scritto con una ricetta vecchia va rifatto, perche' e'
# stato prodotto da regole che non sono piu' le nostre.
c_RicettaVersione = 1

# Il progetto Google a cui l'integrazione fa capo. E' obbligatorio (vol.Required su
# CONF_PROJECT_ID): senza, l'intero dominio non valida e l'assistente resta giu' in silenzio.
# DEVE combaciare con il progetto vero che si crea sulla console.
c_ProjectIdGoogle = "sweetplace-relay"

# La lingua con cui Alexa parla a questa casa. Opzionale, il difetto sarebbe en-US.
c_LocaleAlexa = "it-IT"

# Le chiavi che i due schemi accettano dentro entity_config, e nessun'altra.
#
# Si filtra invece di copiare alla cieca perche' i due file curati NON hanno la stessa forma:
# quello di Google porta anche "room", che lo schema di Alexa non conosce. Copiare tutto vorrebbe
# dire, il giorno che qualcuno allinea i due file curati, generare per Alexa un blocco che non
# valida — e un blocco che non valida non da' errore: fa sparire l'assistente in silenzio.
c_ChiaviAlexa = ("name", "description", "display_categories")
c_ChiaviGoogle = ("name", "aliases", "room")

# Dove finiscono i file che scriviamo. Cartella nostra, separata da quella della lista curata:
# li' dentro c'e' roba mantenuta a mano, qui dentro solo roba generata, e non si confondono.
c_NostraCartella = "sweetplace/sweetlink"
c_NomeAlexa = "alexa.yaml"
c_NomeGoogle = "google_assistant.yaml"

# Le tre radici in cui puo' trovarsi la cartella di configurazione, nello stesso ordine di
# ha/homecontext.py: cercarne una sola farebbe fallire su installazioni dove il resto funziona.
c_Radici = ("/homeassistant", "/home/homeassistant/.homeassistant", "/config")

c_MarcatoreIntestazione = "# generato-da-sweetlink"

_NL = chr(10)


def RadiceConfigurazione():
    for r in c_Radici:
        if os.path.isdir(r):
            return r
    return None


# L'INTESTAZIONE CHE PERMETTE IL CONTROLLO INCROCIATO.
#
# Tre valori, e nessuno dei tre basta da solo: la versione di Home Assistant (uno schema puo'
# cambiare in una correzione di poco conto, o restare identico per dieci versioni), la ricetta con
# cui abbiamo generato (che non sa niente di cosa fa Home Assistant), e la versione dell'add-on
# (che dice chi ha scritto, non se funziona).
# All'avvio si rileggono: se anche uno solo non corrisponde a quello che si vede adesso, il file
# e' stato prodotto per un'altra combinazione e va rifatto.
def Intestazione(haVersione, addonVersione):
    return (
        c_MarcatoreIntestazione
        + " ricetta=" + str(c_RicettaVersione)
        + " ha=" + str(haVersione or "?")
        + " addon=" + str(addonVersione or "?")
    )


# Rilegge i tre valori da un file gia' scritto. Restituisce None se il file non c'e', se non e'
# nostro, o se l'intestazione non si capisce: in tutti e tre i casi la risposta giusta e'
# "rifallo", non "fidati".
def LeggiIntestazione(testo):
    if not isinstance(testo, str):
        return None
    for riga in testo.split(_NL)[:5]:
        if not riga.startswith(c_MarcatoreIntestazione):
            continue
        valori = {}
        for pezzo in riga[len(c_MarcatoreIntestazione):].split():
            if "=" in pezzo:
                k, v = pezzo.split("=", 1)
                valori[k] = v
        if "ricetta" in valori and "ha" in valori and "addon" in valori:
            return valori
    return None


# Le entita' da esporre e le loro etichette, ricavate dalla lista curata.
#
# entity_config viene ridotto alle sole entita' che stanno anche nell'elenco. Non e' pignoleria:
# per Google entity_config E' la lista bianca — con expose_by_default a falso, essere elencati e'
# il modo di essere esposti — quindi una voce di troppo la' dentro esporrebbe un'entita' che la
# lista curata non nomina.
def _voci(curata, chiaviAmmesse):
    if not isinstance(curata, dict):
        return [], {}
    filtro = curata.get("filter")
    elenco = filtro.get("include_entities") if isinstance(filtro, dict) else None
    if not isinstance(elenco, list):
        return [], {}
    elenco = [e for e in elenco if isinstance(e, str) and len(e) > 0]

    sorgente = curata.get("entity_config")
    config = {}
    if isinstance(sorgente, dict):
        for entita in elenco:
            voce = sorgente.get(entita)
            if not isinstance(voce, dict):
                continue
            ridotta = {}
            for k, v in voce.items():
                if k in chiaviAmmesse:
                    ridotta[k] = v
            if len(ridotta) > 0:
                config[entita] = ridotta
    return elenco, config


def _yaml(dati):
    import yaml
    # sort_keys per avere un file identico a parita' di ingresso: cosi' il confronto con quello
    # gia' scritto dice se e' cambiato qualcosa davvero, e non si riscrive per un ordine diverso.
    return yaml.safe_dump(dati, default_flow_style=False, allow_unicode=True, sort_keys=True)


# ALEXA. Se non c'e' niente da esporre si CHIUDE, e chiudere ha una forma precisa.
#
# Non basta un filtro con liste vuote: entityfilter.py:39 calcola empty_filter come "la somma
# delle lunghezze di tutte le liste e' zero", e con empty_filter a vero Alexa salta il filtro ed
# espone tutto. L'unica forma che nega davvero e' un'esclusione NON vuota che prende tutto —
# exclude_entity_globs con "*" — che fa cadere il caso "solo esclusioni" di
# _generate_filter_from_sets_and_pattern_lists, dove tutto cio' che combacia viene escluso.
def GeneraAlexa(curata, haVersione=None, addonVersione=None):
    elenco, config = _voci(curata, c_ChiaviAlexa)
    if len(elenco) == 0:
        smartHome = {"locale": c_LocaleAlexa, "filter": {"exclude_entity_globs": ["*"]}}
    else:
        smartHome = {"locale": c_LocaleAlexa, "filter": {"include_entities": sorted(elenco)}}
        if len(config) > 0:
            smartHome["entity_config"] = config
    return Intestazione(haVersione, addonVersione) + _NL + _yaml({"smart_home": smartHome})


# GOOGLE. Qui la chiave "filter" non esiste e lo schema e' chiuso (extra=vol.PREVENT_EXTRA):
# scriverla farebbe fallire l'intera sezione. La lista bianca si ottiene con expose_by_default a
# falso piu' entity_config, dove essere elencati significa essere esposti.
#
# "expose: true" si scrive ESPLICITO anche se e' il valore predefinito dello schema: l'intenzione
# deve stare nel file, non in un difetto di una libreria che puo' cambiare.
# Nessun service_account: serve solo con report_state acceso, che e' spento per difetto, e
# scriverne uno finto vorrebbe dire spedire un segnaposto inutile su ogni hub venduto.
def GeneraGoogle(curata, haVersione=None, addonVersione=None):
    elenco, config = _voci(curata, c_ChiaviGoogle)
    voci = {}
    for entita in sorted(elenco):
        voce = {}
        for k, v in (config.get(entita) or {}).items():
            voce[k] = v
        voce["expose"] = True
        voci[entita] = voce
    dati = {
        "project_id": c_ProjectIdGoogle,
        "expose_by_default": False,
        # Mappa vuota ESPLICITA quando non c'e' niente da esporre: "entity_config:" senza figli
        # vale None, e una voce nulla dentro questo schema abbatte l'intero dominio.
        "entity_config": voci,
    }
    return Intestazione(haVersione, addonVersione) + _NL + _yaml(dati)


# Scrittura atomica, come per lo schedario degli utenti: temporaneo con nome unico sullo stesso
# filesystem, fsync, e rinomina. Un'interruzione a meta' lascia il file precedente intatto invece
# di lasciarne uno tronco — che qui vorrebbe dire YAML malformato, cioe' Home Assistant che si
# alza in recovery mode senza la casa.
def ScriviAtomico(percorso, testo):
    cartella = os.path.dirname(percorso)
    os.makedirs(cartella, exist_ok=True)
    fd, temporaneo = tempfile.mkstemp(dir=cartella, prefix=".sweetlink-", suffix=".tmp")
    try:
        with io.open(fd, "w", encoding="utf-8", newline=_NL) as f:
            f.write(testo)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporaneo, percorso)
        temporaneo = None
    finally:
        if temporaneo is not None and os.path.exists(temporaneo):
            try:
                os.remove(temporaneo)
            except Exception:
                pass


# Serve riscrivere? Si', se il file non c'e', se non e' nostro, se l'intestazione non combacia con
# la combinazione attuale, o se il contenuto e' cambiato. No in tutti gli altri casi: riscrivere
# per niente vorrebbe dire un riavvio di Home Assistant per niente.
def ServeRiscrivere(percorso, testoNuovo, haVersione, addonVersione):
    try:
        if not os.path.exists(percorso):
            return True, "non c'era"
        vecchio = io.open(percorso, encoding="utf-8").read()
    except Exception as e:
        return True, "non si e' potuto leggere (" + str(e) + ")"

    intestazione = LeggiIntestazione(vecchio)
    if intestazione is None:
        return True, "non e' un file nostro"
    if intestazione.get("ricetta") != str(c_RicettaVersione):
        return True, "ricetta " + str(intestazione.get("ricetta")) + " invece di " + str(c_RicettaVersione)
    if intestazione.get("ha") != str(haVersione or "?"):
        return True, "Home Assistant " + str(intestazione.get("ha")) + " invece di " + str(haVersione)
    if intestazione.get("addon") != str(addonVersione or "?"):
        return True, "add-on " + str(intestazione.get("addon")) + " invece di " + str(addonVersione)
    if vecchio != testoNuovo:
        return True, "la lista e' cambiata"
    return False, "gia' a posto"


# La lista curata di un assistente, letta dalla cartella mantenuta fuori dall'add-on.
# tipo vale "alexa" oppure "google_assistant", che sono i nomi dei due file.
def LeggiCurata(tipo):
    radice = RadiceConfigurazione()
    if radice is None:
        return None
    percorso = os.path.join(radice, "sweetplace", "haconfig", "this-home",
                            "vocal_assistants", tipo + ".yaml")
    if not os.path.exists(percorso):
        return None
    try:
        import yaml
        with io.open(percorso, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except Exception:
        # Chi chiama tratta None come "non lo so", e da li' si chiude. Un file curato illeggibile
        # non deve mai diventare "nessun filtro", che vorrebbe dire casa aperta.
        return None


# Rigenera i due file se serve, e dice se qualcosa e' cambiato.
#
# Restituisce (cambiato, righeDiRegistro). Chi chiama decide cosa fare del "cambiato": finche' il
# configuration.yaml non aggancia questi due file con un !include, non consuma niente nessuno e
# non serve riavviare niente.
def AggiornaSeServe(haVersione, addonVersione):
    radice = RadiceConfigurazione()
    if radice is None:
        return False, ["cartella di configurazione non trovata: non tocco niente"]

    cambiato = False
    righe = []
    for tipo, nome, genera in (
            ("alexa", c_NomeAlexa, GeneraAlexa),
            ("google_assistant", c_NomeGoogle, GeneraGoogle)):
        curata = LeggiCurata(tipo)
        if curata is None:
            righe.append(f"{tipo}: lista curata assente o illeggibile, genero un file che CHIUDE")
        testo = genera(curata, haVersione, addonVersione)
        percorso = os.path.join(radice, c_NostraCartella, nome)
        serve, perche = ServeRiscrivere(percorso, testo, haVersione, addonVersione)
        if not serve:
            righe.append(f"{tipo}: {perche}")
            continue
        try:
            ScriviAtomico(percorso, testo)
            cambiato = True
            righe.append(f"{tipo}: riscritto ({perche})")
        except Exception as e:
            righe.append(f"{tipo}: NON riscritto ({e})")
    return cambiato, righe
