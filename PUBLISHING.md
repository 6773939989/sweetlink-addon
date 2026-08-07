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
