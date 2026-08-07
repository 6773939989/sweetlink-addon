# Pubblicazione dell'add-on

Questo documento spiega il rapporto fra il monorepo privato e il repository pubblico, e come
si pubblica. Vale per chiunque tocchi questo codice.

## I due repository

| | `sweetplace` (privato) | `sweetlink-addon` (pubblico) |
|---|---|---|
| URL | `github.com/6773939989/sweetplace` | `github.com/6773939989/sweetlink-addon` |
| Percorso | cartella `sweetlink/` | root del repo |
| Ruolo | **fonte** — si lavora solo qui | **output generato** |
| Contiene | anche `onboarding/`, `core/`, `installatori/`, `ha-themes/` | solo l'add-on |
| Storia | completa | rigenerata a ogni pubblicazione |

**Regola unica: non si modifica mai niente nel repository pubblico.** Non è protetto da un
divieto tecnico, è protetto dal fatto che qualunque modifica viene sovrascritta alla
pubblicazione successiva, senza avviso e senza possibilità di recuperarla.

## Perché il repository pubblico deve esistere ed essere pubblico

Il Supervisor di Home Assistant clona il repository di un add-on **in anonimo**, senza
credenziali. Un repository privato non è raggiungibile, quindi gli hub non potrebbero né
installare né aggiornare l'add-on.

Il repository pubblico è quindi un requisito tecnico del canale di distribuzione, non una
scelta di apertura del codice. È comunque coerente con la licenza: l'add-on è un fork
AGPL-3.0 di [Homeway.io](https://homeway.io), e l'AGPL impone di fornire il sorgente a chi
riceve il software.

### Cosa è pubblico e cosa no

Pubblico: **solo** il contenuto di `sweetlink/`, cioè l'add-on.
Privato: tutto il resto del monorepo — il backend cloud `onboarding/`, `core/`,
`installatori/`, `ha-themes/`. Su GitHub la visibilità è del repository, non della cartella:
non esiste una cartella pubblica dentro un repo privato, e viceversa.

## Come si pubblica

**Automaticamente**, tramite `.github/workflows/publish-sweetlink.yaml` nel monorepo: a ogni
push che tocca `sweetlink/` il workflow clona il repo pubblico, ne sostituisce il contenuto e
committa, dopo aver verificato il bump di versione (vedi sotto).

Non usa `git subtree push`: la split ricalcola i commit dalla storia del monorepo, quindi un
rebase o un amend cambierebbe le SHA, la storia pubblicata divergerebbe e il push verrebbe
rifiutato. Sostituire il contenuto e' invece sempre un fast-forward. Autenticazione gia' configurata tramite una deploy key SSH scrivibile installata su
`sweetlink-addon`, con la chiave privata nel secret `SWEETLINK_DEPLOY_KEY` del monorepo: il
`GITHUB_TOKEN` del monorepo non puo' scrivere su un altro repository.

### Il guard-rail sul bump di versione

Il workflow confronta l'**oggetto-albero** di `addon/` con quello gia' pubblicato:

| Contenuto di `addon/` | `version:` | Esito |
|---|---|---|
| identico | qualsiasi | pubblica |
| cambiato | incrementata | pubblica |
| cambiato | **invariata** | **blocca** |

Il motivo: il Supervisor decide se offrire un aggiornamento confrontando le stringhe di
`version:`. Pubblicare codice nuovo senza bump lo rende invisibile agli hub, in silenzio.
Per le modifiche che non toccano il funzionamento si puo' forzare, lanciando il workflow a
mano con `force=true`.

### A mano

Dal monorepo, con il lavoro già committato:

Dal monorepo, con il lavoro già committato:

```bash
# 1. genera la storia della sola cartella sweetlink/
git subtree split --prefix=sweetlink -b split/sweetlink

# 2. pubblica
git push sweetlink-pub split/sweetlink:refs/heads/main
```

Se il remote non è configurato:

```bash
git remote add sweetlink-pub https://github.com/6773939989/sweetlink-addon
```

> ⚠️ Il prefisso è `sweetlink`, **non** `sweetlink/addon`. Con il prefisso sbagliato il repo
> risultante avrebbe `config.yaml` alla root e nessun `repository.yaml`: Home Assistant non lo
> riconoscerebbe come add-on repository.

### Verifica dopo la pubblicazione

```bash
gh api repos/6773939989/sweetlink-addon/contents/repository.yaml   # deve esistere
gh api repos/6773939989/sweetlink-addon/contents/addon/config.yaml # deve esistere
```

`repository.yaml` deve stare **alla root** e la cartella dell'add-on al **primo livello**:
sono i due requisiti strutturali di Home Assistant.

## Prima di ogni rilascio

1. **Incrementare `version:`** in `addon/config.yaml`. Il Supervisor confronta questa stringa
   con quella installata per decidere se offrire un aggiornamento: se non cambia, **nessun hub
   vedrà mai il nuovo codice**.
2. **Aggiornare `addon/CHANGELOG.md`**: è il testo che il Supervisor mostra all'utente nel
   dialogo di aggiornamento.
3. Se l'immagine prebuilt è attiva, il tag dell'immagine su GHCR deve corrispondere
   **esattamente** alla stringa di `version:`.

## Lo stato della riga `image:`

`addon/config.yaml` contiene `image:` **commentata di proposito**.

- **Commentata** (stato attuale): Home Assistant costruisce l'add-on direttamente sull'hub. Funziona,
  ma su un Raspberry Pi richiede una quindicina di minuti a ogni installazione e a ogni
  aggiornamento, perché compila `aiohttp` e `zstandard` e scarica `cloudflared` da GitHub.
- **Scommentata senza che l'immagine esista**: il Supervisor tenta il pull, non trova nulla, e
  **l'installazione fallisce**.

Va scommentata solo **dopo** che la CI ha pubblicato un'immagine per la versione dichiarata,
mai prima.

Sulla visibilita' del package non serve fare niente: pubblicando da un repository pubblico con
il `GITHUB_TOKEN`, il package su GHCR eredita la visibilita' del repository ed e' scaricabile
in anonimo. Verificato con un pull anonimo delle immagini 2.7.41, che risponde `HTTP 200`.

## Accessi al repository pubblico

Scrittura: **solo il proprietario**. Nuovi collaboratori possono essere aggiunti unicamente
dal proprietario, dalle impostazioni del repository.

Issues, Pull Request, Wiki e Projects sono **disattivati**: dall'esterno il repository è in
sola lettura e non accetta nessuna forma di contributo. Il branch `main` è protetto contro
cancellazione e force-push.

## Come l'aggiornamento arriva davvero sugli hub

Pubblicare non basta. Verificato sul sorgente del Supervisor
(`supervisor/misc/tasks.py`, `supervisor/apps/app.py`, `supervisor/apps/validate.py`):

1. **Bump di `version:`** — e' il trigger. Senza, non succede nulla.
2. **La CI del repo pubblico costruisce l'immagine** taggata con quella versione esatta.
3. **Il Supervisor ricarica lo store** ogni **3 ore** (`RUN_RELOAD_APPS = 10800`).
4. **Il Supervisor valuta l'aggiornamento** ogni **16 ore** (`RUN_UPDATE_APPS = 57600`), e
   installa solo se tutte queste condizioni sono vere:
   - `auto_update` e' attivo per quell'add-on. **Il default e' `False`** ed e'
     un'impostazione **per installazione**: l'autore non puo' forzarla dal `config.yaml`.
   - l'aggiornamento non attraversa una voce di `breaking_versions`, opzione dichiarabile
     dall'autore in `config.yaml` per bloccare i salti delicati.
   - la nuova versione e' pubblicata **da almeno 24 ore**: il Supervisor impone una
     quarantena deliberata.

Nel caso migliore passano quindi fra le **24 e le 40 ore** dalla pubblicazione.

> ### Per il modello a immagine clonata
> Gli hub nascono da un'immagine golden clonata su SD. **Attivare `auto_update` sull'add-on
> nell'immagine golden prima di clonarla**: ogni hub spedito lo eredita gia' attivo. E' una
> spunta sola che copre tutto il parco futuro. Senza, ogni hub resterebbe fermo alla versione
> installata finche' qualcuno non aggiorna a mano dalla UI.
