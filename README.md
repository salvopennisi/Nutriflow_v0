# Nutriflow 🍏

**Nutriflow** è un'applicazione web in Python/Streamlit per supportare il lavoro operativo di un nutrizionista: gestione degli assistiti, stima e aggiornamento del fabbisogno energetico, rilevazioni biometriche, analisi della composizione corporea, costruzione di piani alimentari e gestione di workout con monitoraggio della progressione.

> Questo README è stato riallineato al comportamento effettivamente presente nel codice del repository. Le funzionalità non implementate o disponibili solo a livello di service sono indicate esplicitamente.

---

## Indice

- [Stato attuale](#stato-attuale)

- [Funzionalità implementate](#funzionalità-implementate)

- [User Stories](#user-stories)

- [Architettura](#architettura)

- [Struttura del progetto](#struttura-del-progetto)

- [Modello dati](#modello-dati)

- [Logiche di calcolo](#logiche-di-calcolo)

- [Installazione ed esecuzione](#installazione-ed-esecuzione)

- [Test](#test)

- [Known gaps e debito tecnico](#known-gaps-e-debito-tecnico)

---

## Stato attuale

Il progetto è attualmente una **web app multipagina Streamlit** con accesso diretto a **PostgreSQL** tramite `psycopg2`.

Il flusso principale è:

```text

Streamlit UI

    │

    ├── pages/1_pazienti.py

    ├── pages/2_biometria.py

    ├── pages/3_piani_alimentari.py

    ├── pages/4_workout.py

    └── pages/5_catalogo_alimenti.py

    │

    ▼

Backend/services/*.py

    │

    ▼

Common/functions.py

    │

    ▼

PostgreSQL

```

Non è presente un API server applicativo: `Backend/main.py` è attualmente vuoto e le pagine Streamlit invocano direttamente i service Python.

### Moduli realmente disponibili in UI

| Modulo | Stato | Funzioni principali |

|---|---|---|

| Pazienti | ✅ Implementato | Creazione, ricerca, modifica, TDEE automatico/manuale |

| Biometria | ✅ Implementato | Misure antropometriche, 7 pliche, BF%, proporzioni classiche/auree, storico |

| Piani alimentari | ✅ Implementato | Budget settimanale indipendente, distribuzione 7×5, confronto Budget/assegnato, macro, import/clone/update, lista spesa, PDF, micronutrienti |

| Workout | ✅ Implementato | CRUD piani, esercizi/blocchi, tecniche, registrazione performance e trend |

| Catalogo alimenti | ✅ Implementato | UI dedicata per lista, ricerca, inserimento, modifica, eliminazione e import massivo CSV/JSON |

| Riferimenti micronutrienti | ⚙️ Service-only | CRUD tipologica disponibile nel backend |

| Anamnesi strutturata | ⚙️ Parziale | Service per domande/risposte presente; UI attuale usa soprattutto campi testuali liberi |

| Autenticazione/ruoli | 🚧 Non implementato | Esiste la tabella `users`, ma la UI usa un `user_id` placeholder |

| Ricette | ❌ Non implementato | Non sono presenti service/UI attivi per ricette |

---

# Funzionalità implementate

## 1. Gestione Pazienti 👥

La pagina `pages/1_pazienti.py` consente di:

- creare un nuovo assistito;

- registrare nome, cognome, data di nascita, sesso, altezza e peso iniziale;

- associare stile di vita/PAL, professione, storia/anamnesi libera e patologie/note cliniche;

- ricercare gli assistiti per nome, cognome o professione;

- modificare i dati di un assistito esistente;

- visualizzare l'ultimo peso disponibile ricavato dallo storico biometrico;

- calcolare e memorizzare il TDEE;

- applicare una correzione professionale percentuale al TDEE stimato;

- salvare un TDEE manuale quando il professionista vuole sostituire la stima algoritmica con un valore osservato;

- registrare un nuovo peso nello storico biometrico quando, durante il ricalcolo TDEE, il peso è effettivamente cambiato.

### TDEE

Il codice implementa la stima del metabolismo basale tramite la formula:

```text

BMR = 10 × peso_kg + 6.25 × altezza_cm - 5 × età + costante_sesso

```

con:

- `+5` per Maschio;

- `-161` per Femmina.

Il TDEE viene quindi calcolato come:

```text

TDEE_stimato = BMR × PAL

TDEE_adottato = TDEE_stimato × fattore_correzione_professionista

```

Fattori PAL implementati:

| Livello attività | PAL |

|---|---:|

| Sedentario | 1.20 |

| Leggermente attivo | 1.30 |

| Moderatamente attivo | 1.425 |

| Molto attivo | 1.575 |

| Estremamente attivo | 1.725 |

La correzione professionista è configurabile nell'intervallo `-30% / +30%`.

---

## 2. Biometria e composizione corporea 📈

La pagina `pages/2_biometria.py` consente di selezionare un assistito e lavorare sul relativo storico antropometrico.

### Ultima rilevazione

La UI mostra una sintesi dell'ultima misurazione disponibile, inclusi quando presenti:

- peso;

- massa grassa BF%;

- circonferenza vita;

- torace;

- spalle;

- dettaglio completo degli altri parametri registrati.

### Nuova misurazione

È possibile registrare:

- peso;

- vita;

- fianchi;

- torace;

- spalle;

- collo;

- polso;

- braccia;

- avambracci;

- coscia;

- polpacci;

- data di riferimento;

- sette pliche cutanee.

### Calcolo BF% - Jackson-Pollock 7 pliche

Il backend implementa il calcolo della densità corporea tramite **Jackson-Pollock 7 pliche**, con formule distinte per sesso, e converte la densità in BF% con la formula di Siri.

Le pliche richieste sono:

- petto;

- addome;

- coscia;

- tricipite;

- sovrascapolare;

- sovrailiaca;

- ascellare.

### Proporzioni classiche e auree

Il backend implementa due modalità di confronto tra misure reali e target teorici:

1. **Metodo basato sul polso**, con rapporti di proporzione classica/McCallum;

2. **Metodo basato sulla vita**, che utilizza il rapporto aureo `φ ≈ 1.618` e, quando disponibile, anche il rapporto vita/altezza.

La UI permette di:

- selezionare un intervallo temporale;

- scegliere una rilevazione corrente;

- confrontarla con la prima rilevazione del periodo;

- visualizzare target ideale, misura attuale e misura iniziale.

### Storico

Il service espone l'intero storico biometrico del paziente e la UI ne visualizza il dettaglio tabellare. Sono inoltre presenti i componenti per i trend di peso e BF%.

---

## 3. Piani Alimentari 🥗

La pagina `pages/3_piani_alimentari.py` è il modulo più articolato dell'applicazione e separa esplicitamente **Budget alimentare**, **Distribuzione settimanale** e **Persistenza** per ridurre i problemi di sincronizzazione legati ai rerun di Streamlit.

### Gestione dei piani esistenti

Per ogni assistito è possibile:

- visualizzare i piani alimentari già salvati;

- espandere il dettaglio del singolo piano;

- visualizzare alimenti, quantità e macro calcolati;

- eliminare definitivamente un piano con conferma esplicita;

- generare un PDF;

- visualizzare la lista della spesa aggregata;

- calcolare on-demand l'overview dei micronutrienti.

### Budget alimentare settimanale

Prima della distribuzione sui singoli giorni l'utente può costruire un **Budget alimentare settimanale indipendente dai giorni**, indicando gli alimenti e la quantità complessiva prevista per l'intera settimana.

Il riepilogo del Budget utilizza come prime colonne informative:

| Campo | Significato |

|---|---|

| N. volte | Numero di occorrenze dell'alimento nella Distribuzione settimanale con grammatura maggiore di 0 |

| Alimento | Alimento presente nel Budget |

| Budget settimanale (g) | Quantità totale prevista per la settimana |

| Assegnati (g) | Somma delle grammature realmente presenti nelle grid giorno/pasto |

| Residui (g) | Differenza tra Budget e quantità assegnata |

Il residuo viene sempre derivato come:

```text

Residuo = Budget settimanale - Assegnati

```

Il valore può quindi essere:

- positivo quando la quantità assegnata è inferiore al Budget;

- zero quando Budget e Distribuzione coincidono;

- negativo quando la quantità distribuita supera il Budget.

A destra della grid vengono mostrate le medie giornaliere del Budget:

- kcal;

- carboidrati;

- grassi;

- proteine.

Le medie sono calcolate come:

```text

media giornaliera = totale nutrizionale del Budget settimanale / 7

```

Il Budget diventa consolidato solo tramite il pulsante **Aggiorna i valori medi del Budget alimentare**.

Alla pressione del pulsante il sistema:

1. acquisisce l'ultima versione della grid Budget;

2. ricalcola i macro e le medie giornaliere del Budget;

3. legge direttamente le grammature RAW presenti in tutte le grid giorno/pasto;

4. aggrega per alimento i grammi realmente assegnati;

5. aggiorna **N. volte**, **Assegnati** e **Residui**;

6. classifica ogni alimento come sotto Budget, coerente, sopra Budget o fuori Budget.

Questa azione **non ricalcola i macro della Distribuzione settimanale**.

### Distribuzione settimanale

Dopo aver definito il Budget, il piano viene distribuito su **7 giorni** e **5 pasti**:

```text

Lunedì ... Domenica

├── Colazione

├── Spuntino

├── Pranzo

├── Merenda

└── Cena

```

Per ogni pasto l'utente può:

- aggiungere nuove righe;

- rimuovere righe;

- cercare/selezionare un alimento tramite autocomplete AG Grid;

- indicare i grammi;

- vedere kcal, carboidrati, grassi e proteine calcolati proporzionalmente.

L'autocomplete della Distribuzione propone gli alimenti consolidati nel Budget settimanale.

I macronutrienti degli alimenti sono considerati valori per **100 g** e vengono ricalcolati in funzione della quantità inserita.

La Distribuzione dispone di un proprio comando indipendente: **Aggiorna valori medi della Distribuzione settimanale**.

Alla pressione del pulsante vengono aggiornati esclusivamente:

- macro per pasto;

- aggregazioni giornaliere;

- aggregazioni settimanali;

- medie nutrizionali della Distribuzione.

L'operazione **non esegue alcun controllo rispetto al Budget alimentare** e non modifica N. volte, Assegnati o Residui del riepilogo Budget.

### Separazione dei cicli di aggiornamento

Budget, Distribuzione e salvataggio sono trattati come tre dinamiche distinte:

| Azione | Budget | Distribuzione | Check coerenza |

|---|---:|---:|---:|

| Aggiorna i valori medi del Budget alimentare | ✅ Aggiorna | ❌ Non ricalcola i macro | ✅ Confronto informativo |

| Aggiorna valori medi della Distribuzione settimanale | ❌ Non modifica | ✅ Aggiorna | ❌ Nessun controllo |

| Aggiorna/Salva piano | ✅ Legge RAW corrente | ✅ Legge RAW corrente | ✅ Bloccante |

Questa separazione evita che una modifica in una sezione provochi automaticamente il ricalcolo dell'altra durante i rerun Streamlit.

### Aggregazioni della Distribuzione

Il modulo mantiene aggregazioni a tre livelli:

- **per pasto**;

- **per giorno**;

- **per settimana**.

L'overview settimanale della Distribuzione mostra:

- kcal totali della settimana;

- kcal medie giornaliere;

- carboidrati medi giornalieri;

- grassi medi giornalieri;

- proteine medie giornaliere.

### Import, modifica e clonazione

È possibile cercare e importare nell'editor un piano già assegnato allo stesso assistito.

Dopo l'import l'utente può:

- aggiornare il piano originale;

- usare il piano come base e salvarlo come **nuovo piano**;

- ripartire da un piano vuoto.

All'apertura di un piano esistente:

- le grid giorno/pasto vengono ricostruite dagli item persistiti;

- il Budget viene ricostruito aggregando le grammature salvate per alimento;

- **Assegnati** viene inizializzato con le quantità già persistite;

- **N. volte** viene ricostruito contando le occorrenze con grammatura maggiore di 0.

Poiché il Budget non dispone ancora di una persistenza DB separata, per un piano riaperto il target iniziale del Budget coincide con la somma delle allocazioni salvate.

Il backend applica l'unicità del nome del piano **per assistito**, ignorando differenze di maiuscole/minuscole e spazi esterni.

### Controllo di coerenza prima del salvataggio

Le azioni:

- **Aggiorna piano alimentare esistente**;

- **Salva come nuovo piano alimentare**;

eseguono sempre un controllo di coerenza indipendente sui dati RAW correnti, anche se l'utente non ha premuto prima i pulsanti di aggiornamento del Budget o della Distribuzione.

Per ogni alimento deve essere verificata la condizione:

```text

Budget settimanale = Σ grammature realmente assegnate nei giorni/pasti

```

Il salvataggio viene bloccato quando:

- Assegnati < Budget;

- Assegnati > Budget;

- esiste un alimento nella Distribuzione ma non nel Budget;

- il Budget è vuoto;

- un alimento utilizzato non è più presente nel catalogo corrente.

In presenza di incoerenze viene mostrato un warning e una tabella con il dettaglio degli alimenti non coerenti.

Solo dopo il superamento del controllo vengono consolidati Budget e Distribuzione e viene costruito il payload persistibile.

### Persistenza atomica

Creazione e aggiornamento di un piano avvengono in transazione.

In aggiornamento il dettaglio viene sostituito completamente:

```text

UPDATE diet_plans

DELETE old diet_meal_items

INSERT new diet_meal_items

COMMIT

```

In caso di errore viene effettuato il rollback dell'intera operazione.

### Lista della spesa

La lista della spesa aggrega le quantità dello stesso alimento sull'intera settimana e produce una tabella del tipo:

| Alimento | Quantità totale (g) |

|---|---:|

| Riso | 700 |

| Pollo | 900 |

| ... | ... |

La stessa aggregazione viene inclusa nel PDF del piano.

### Esportazione PDF

Il PDF viene generato in memoria con **ReportLab** e contiene:

1. nome del piano e assistito;

2. descrizione/obiettivi;

3. eventuali avvertenze o note cliniche;

4. macro aggregati per giorno;

5. lista della spesa settimanale;

6. una pagina di dettaglio per ogni giorno con pasti, alimenti, grammi e macro;

7. numerazione pagina e footer.

### Overview micronutrienti

Il calcolo è eseguito **on-demand** e non ad ogni modifica dell'editor, per evitare query e ricalcoli inutili.

Il backend:

- recupera dal catalogo `foods` i micronutrienti per 100 g;

- calcola l'apporto totale del piano;

- divide il totale per 7 giorni;

- confronta l'apporto medio giornaliero con la tipologica `micronutrients_quantities`;

- gestisce riferimenti per MJ trasformandoli in funzione dell'energia media effettiva della dieta;

- evita confronti quando l'unità/base nutrizionale del dato non è semanticamente compatibile con il riferimento.

Sono mappati **25 micronutrienti**, tra vitamine, minerali, omega-3 e omega-6.

Stati visualizzati in UI:

| Stato | Significato |

|---|---|

| 🟢 OK | Riferimento rispettato |

| 🟠 LOW_PRI / LOW_AI | Apporto sotto PRI/AI |

| 🔴 HIGH_UL | Superamento di un limite UL applicabile |

| 🟡 HIGH_WARNING | Superamento di un safe level/livello prudenziale |

| 🩶 NOT_COMPARABLE | Base nutrizionale non confrontabile |

| 🩶 NO_REFERENCE | Riferimento non configurato |

---

## 4. Catalogo alimenti e tipologiche ⚙️

`Backend/services/food_service.py` implementa operazioni CRUD per:

- categorie alimentari;

- alimenti;

- micronutrienti/riferimenti nutrizionali.

Il catalogo alimenti supporta:

- catalogo condiviso tra i nutrizionisti nell'MVP;

- ricerca, inserimento, modifica ed eliminazione dalla UI `pages/5_catalogo_alimenti.py`;

- import massivo da CSV o JSON con anteprima e validazione;

- gestione dei duplicati per nome tramite strategia `skip` o `update`;

- macronutrienti;

- indice glicemico;

- vitamine;

- minerali;

- omega-3 e omega-6;

- peculiarità nutrizionali.

> La separazione dei cibi per `user_id` non fa parte dell'MVP. La colonna resta nel modello come predisposizione futura, ma il catalogo corrente non applica filtri o ownership per nutrizionista.

---

## 5. Anamnesi strutturata ⚙️

`patient_service.py` contiene service per:

- recuperare un template di domande di anamnesi ordinato per `order_index`;

- salvare le risposte di un assistito sostituendo atomicamente il set precedente.

Le tabelle coinvolte sono:

- `anamnesis_questions`;

- `anamnesis_answers`.

> La UI corrente non espone ancora il questionario strutturato: nella pagina Pazienti sono presenti principalmente `descrizione_storia` e `patologie` come campi testuali liberi.

---

## 6. Workout e progressione 🏋️

La pagina `pages/4_workout.py` introduce un modulo dedicato alla prescrizione e al monitoraggio dell'allenamento dell'assistito.

Il modulo utilizza il service `Backend/services/workout_service.py` e il modello dati a tre tabelle:

```text

workout_plans

      │ 1:N

      ▼

workout_exercises

      │ 1:N

      ▼

workout_measurements

```

### Listing e gestione dei workout

Per ogni assistito è possibile:

- visualizzare i workout attivi;

- includere nel listing anche i workout archiviati;

- creare un nuovo workout;

- modificare un workout esistente;

- archiviare logicamente un workout senza perdere esercizi e misurazioni;

- ripristinare un workout archiviato;

- visualizzare il numero di esercizi, il numero di misurazioni e la data dell'ultima performance registrata.

Il nome del workout è univoco per assistito tra i workout attivi, ignorando maiuscole/minuscole e spazi esterni.

### Prescrizione degli esercizi

Ogni workout contiene una lista ordinata di esercizi. Per ciascun esercizio è possibile configurare:

- serie target;

- range di ripetizioni minimo/massimo;

- carico target;

- TUT target;

- recupero target;

- RIR e RPE target;

- note;

- una o più metriche di progressione.

Metriche supportate:

```text

LOAD

VOLUME

TUT

DENSITY

REPS

```

### Blocchi multi-esercizio

Gli esercizi possono essere raggruppati tramite `block_id` e rappresentati nella UI con una semplice etichetta di blocco, ad esempio `A`, `B`, `C`.

Tipologie supportate:

```text

STANDARD

SUPERSET

JUMP_SET

TRI_SET

GIANT_SET

CIRCUIT

```

Gli esercizi appartenenti allo stesso blocco condividono lo stesso `block_id` e possono specificare ordine e numero di round.

### Tecniche di allenamento

La modalità con cui un singolo esercizio viene eseguito è modellata separatamente dal blocco multi-esercizio.

Tecniche supportate:

```text

STANDARD

DROP_SET

REST_PAUSE

MYO_REPS

CLUSTER

AMRAP

TEMPO

PAUSE_REPS

PARTIAL_REPS

BACK_OFF

```

I parametri specifici delle tecniche sono salvati nel campo `technique_params` in formato `JSONB`, evitando di introdurre colonne dedicate per ogni tecnica.

Esempio:

```json

{

  "drops": 2,

  "load_reduction_pct": 20

}

```

### Modifica senza perdita dello storico

A differenza del dettaglio dei piani alimentari, durante l'aggiornamento di un workout gli esercizi non vengono eliminati e reinseriti.

La strategia è:

```text

esercizio esistente         → UPDATE mantenendo lo stesso UUID

nuovo esercizio             → INSERT

esercizio rimosso dal piano → archived_at valorizzato

```

In questo modo le foreign key presenti in `workout_measurements` restano valide e lo storico delle performance non viene perso.

### Registrazione delle performance

La sezione **Progressi** permette di registrare l'esecuzione reale di ciascun esercizio.

Una seduta viene identificata tramite `execution_id`. Lo stesso identificativo può essere riutilizzato per tutti gli esercizi svolti nella medesima sessione di allenamento.

La granularità della misurazione è:

```text

1 riga workout_measurements = 1 serie / segmento eseguito

```

Campi principali registrabili:

- numero serie;

- numero segmento;

- ripetizioni eseguite;

- carico;

- TUT;

- recupero;

- durata attiva;

- RIR;

- RPE;

- note.

`segment_number` permette di rappresentare tecniche che suddividono una stessa serie in più parti, ad esempio drop set, rest-pause o cluster.

Esempio drop set:

```text

Serie | Segmento | Reps | Carico

------|----------|------|-------

1     | 1        | 8    | 100 kg

1     | 2        | 6    |  80 kg

1     | 3        | 8    |  60 kg

```

### Trend e metriche derivate

Il backend aggrega le misurazioni per `execution_id` e calcola:

- serie completate;

- ripetizioni totali;

- carico massimo;

- volume;

- TUT totale;

- recupero totale;

- durata attiva totale;

- densità;

- RIR medio;

- RPE medio.

La UI consente di visualizzare trend temporali per:

- carico massimo;

- volume;

- TUT;

- densità;

- ripetizioni.

È inoltre disponibile lo storico dettagliato di ogni serie/segmento registrato.

---

# User Stories

Legenda stato:

- **UI** = disponibile nell'interfaccia Streamlit;

- **BE** = implementata nel backend ma non esposta da una pagina dedicata;

- **PARZIALE** = codice presente ma con gap tecnici indicati nella sezione finale;

- **BACKLOG** = user story definita ma non ancora implementata nel codice corrente.

## Epic A - Gestione assistiti

### US-PAT-01 - Creazione assistito `[UI]`

**Come nutrizionista, voglio creare la scheda di un nuovo assistito con dati anagrafici, antropometrici e clinici di base, così da poterlo gestire nei successivi moduli di biometria e pianificazione alimentare.**

Criteri principali:

- nome e cognome obbligatori;

- data di nascita, altezza, peso, sesso, stile di vita e professione opzionali;

- possibilità di inserire storia/anamnesi libera e patologie/note;

- il peso iniziale può generare la prima rilevazione biometrica.

### US-PAT-02 - Ricerca assistiti `[UI]`

**Come nutrizionista, voglio filtrare rapidamente gli assistiti per nome, cognome o professione, così da trovare la scheda su cui lavorare.**

### US-PAT-03 - Modifica assistito `[UI]`

**Come nutrizionista, voglio aggiornare i dati di un assistito esistente, così da mantenere la scheda coerente con la situazione corrente.**

### US-PAT-04 - Calcolo TDEE `[UI]`

**Come nutrizionista, voglio calcolare il TDEE a partire da età, sesso, peso, altezza e PAL, così da ottenere una stima del fabbisogno energetico di mantenimento.**

### US-PAT-05 - Correzione professionale TDEE `[UI]`

**Come nutrizionista, voglio applicare una correzione percentuale alla stima del TDEE, così da adattare il risultato alla mia valutazione professionale.**

### US-PAT-06 - Override manuale TDEE `[UI]`

**Come nutrizionista, voglio salvare direttamente un TDEE manuale, così da utilizzare un valore di mantenimento osservato senza dipendere dalla formula.**

### US-PAT-07 - Allineamento peso/TDEE `[UI]`

**Come nutrizionista, voglio che un nuovo peso venga registrato nello storico quando aggiorno il TDEE calcolato e il peso è cambiato, così da non perdere l'evoluzione antropometrica dell'assistito.**

---

## Epic B - Biometria

### US-BIO-01 - Ultima misurazione `[UI]`

**Come nutrizionista, voglio vedere immediatamente l'ultima rilevazione biometrica dell'assistito, così da avere un riepilogo della situazione corrente.**

### US-BIO-02 - Registrazione antropometrica `[UI/PARZIALE]`

**Come nutrizionista, voglio registrare peso, circonferenze e pliche con una data di riferimento, così da costruire uno storico misurabile nel tempo.**

### US-BIO-03 - Calcolo BF% `[UI]`

**Come nutrizionista, voglio ottenere la percentuale di massa grassa dalle sette pliche tramite Jackson-Pollock e Siri, così da associare alla rilevazione una stima standardizzata della BF%.**

### US-BIO-04 - Confronto proporzioni classiche `[UI]`

**Come nutrizionista, voglio confrontare le misure corporee reali con target proporzionali basati sul polso, così da analizzare lo scostamento rispetto al modello classico.**

### US-BIO-05 - Confronto proporzioni auree `[UI]`

**Come nutrizionista, voglio confrontare vita, torace/spalle e coscia con rapporti basati sulla sezione aurea, così da disporre di un secondo modello comparativo.**

### US-BIO-06 - Confronto temporale `[UI]`

**Come nutrizionista, voglio filtrare un periodo e confrontare una rilevazione scelta con quella iniziale, così da valutare l'evoluzione delle misure.**

### US-BIO-07 - Storico e trend `[UI/PARZIALE]`

**Come nutrizionista, voglio consultare lo storico biometrico e visualizzare i trend di peso e BF%, così da monitorare l'andamento dell'assistito nel tempo.**

---

## Epic C - Piani alimentari

### US-DIET-01 - Consultazione piani `[UI]`

**Come nutrizionista, voglio vedere tutti i piani associati a un assistito, così da accedere rapidamente allo storico delle prescrizioni.**

### US-DIET-02 - Creazione piano settimanale `[UI]`

**Come nutrizionista, voglio costruire un piano su sette giorni e cinque pasti al giorno, così da rappresentare una settimana alimentare completa.**

### US-DIET-03 - Gestione dinamica righe `[UI]`

**Come nutrizionista, voglio aggiungere e rimuovere righe in ciascun pasto, così da adattare liberamente il numero di alimenti previsto.**

### US-DIET-04 - Ricerca alimento con autocomplete `[UI]`

**Come nutrizionista, voglio ricercare un alimento direttamente nella griglia, così da selezionare rapidamente una voce valida del catalogo.**

### US-DIET-05 - Calcolo proporzionale macro `[UI]`

**Come nutrizionista, voglio che kcal e macronutrienti siano ricalcolati in funzione dei grammi inseriti, così da vedere l'impatto nutrizionale reale della quantità prescritta.**

### US-DIET-06 - Aggregazione pasto/giorno/settimana `[UI]`

**Come nutrizionista, voglio consolidare i macro per pasto, giorno e settimana, così da controllare l'equilibrio complessivo del piano.**

### US-DIET-07 - Import piano esistente `[UI]`

**Come nutrizionista, voglio importare nell'editor un piano già assegnato all'assistito, così da modificarlo senza ricostruirlo da zero.**

### US-DIET-08 - Aggiornamento piano `[UI]`

**Come nutrizionista, voglio modificare e sovrascrivere un piano esistente in modo atomico, così da evitare stati parzialmente aggiornati.**

### US-DIET-09 - Clonazione piano `[UI]`

**Come nutrizionista, voglio partire da un piano esistente e salvarlo come nuovo, così da creare varianti mantenendo l'originale.**

### US-DIET-10 - Unicità nome per assistito `[UI/BE]`

**Come nutrizionista, voglio evitare due piani con lo stesso nome per lo stesso assistito, così da ridurre ambiguità nella consultazione dello storico.**

### US-DIET-11 - Eliminazione controllata `[UI]`

**Come nutrizionista, voglio eliminare un piano solo dopo una conferma esplicita, così da ridurre il rischio di cancellazioni accidentali.**

### US-DIET-12 - Lista della spesa `[UI]`

**Come nutrizionista, voglio ottenere la quantità settimanale aggregata per alimento, così da fornire una lista della spesa direttamente derivata dal piano.**

### US-DIET-13 - Esportazione PDF `[UI]`

**Come nutrizionista, voglio esportare il piano in un PDF leggibile e strutturato, così da poterlo consegnare all'assistito.**

### US-DIET-14 - Overview micronutrienti `[UI]`

**Come nutrizionista, voglio calcolare su richiesta l'apporto medio giornaliero dei micronutrienti, così da effettuare un controllo qualitativo più profondo del piano.**

### US-DIET-15 - Confronto con riferimenti nutrizionali `[UI/BE]`

**Come nutrizionista, voglio confrontare gli apporti con PRI, AI, UL o safe level solo quando le basi nutrizionali sono compatibili, così da evitare alert fuorvianti.**

### US-DIET-16 - Diagnostica editor `[UI]`

**Come sviluppatore/manutentore, voglio poter visualizzare e scaricare il log AG Grid → Python, così da diagnosticare problemi di sincronizzazione tra modifiche della griglia e stato server-side.**

### US-DIET-17 - Budget alimentare settimanale indipendente dai giorni `[UI]`

**Come nutrizionista, voglio definire gli alimenti e le relative quantità complessive della settimana senza assegnarli preventivamente a giorni specifici, così da costruire prima il fabbisogno alimentare complessivo e solo successivamente distribuirlo nella settimana.**

Criteri principali:

- deve essere disponibile una sezione Budget indipendente dalle grid dei singoli giorni;

- per ogni alimento deve essere indicabile la quantità settimanale prevista in grammi;

- devono essere calcolati i macro complessivi del Budget;

- devono essere mostrate kcal, carboidrati, grassi e proteine medie giornaliere come totale settimanale / 7.

### US-DIET-18 - Distribuzione libera del Budget nella settimana `[UI]`

**Come nutrizionista, voglio distribuire liberamente gli alimenti definiti nel Budget nei diversi giorni e pasti della settimana, così da costruire il piano giornaliero mantenendo il riferimento alle quantità settimanali pianificate.**

Criteri principali:

- la Distribuzione resta organizzata per giorno, pasto, alimento e grammatura;

- gli alimenti consolidati nel Budget devono essere utilizzabili nella Distribuzione;

- la quantità assegnata deve essere ricavabile aggregando tutte le grammature dello stesso alimento presenti nei 7 giorni e 5 pasti.

### US-DIET-19 - Riepilogo Budget con occorrenze, assegnati e residui `[UI]`

**Come nutrizionista, voglio confrontare il Budget settimanale con quanto realmente distribuito nei giorni, così da capire immediatamente se ogni alimento è stato assegnato correttamente e con quale frequenza compare nel piano.**

Criteri principali:

- la prima colonna visibile del riepilogo deve essere **N. volte**;

- N. volte deve contare quante righe della Distribuzione contengono l'alimento con grammatura > 0;

- devono essere mostrati almeno Alimento, Budget, Assegnati e Residui;

- il Residuo deve essere calcolato come `Budget - Assegnati`;

- devono essere distinguibili i casi Assegnati < Budget, Assegnati = Budget, Assegnati > Budget e alimento fuori Budget.

### US-DIET-20 - Aggiornamento esplicito del Budget alimentare `[UI]`

**Come nutrizionista, voglio aggiornare esplicitamente i valori del Budget tramite un pulsante dedicato, così da evitare sincronizzazioni implicite e problemi tipici dei rerun di Streamlit.**

Criteri principali:

- deve essere disponibile il pulsante **Aggiorna i valori medi del Budget alimentare**;

- il pulsante deve acquisire l'ultimo draft del Budget e ricalcolarne macro e medie;

- deve leggere le grammature RAW della Distribuzione e aggiornare N. volte, Assegnati e Residui;

- deve classificare le quantità assegnate come inferiori, uguali o superiori al Budget ed evidenziare gli alimenti fuori Budget;

- non deve ricalcolare i macro della Distribuzione settimanale.

### US-DIET-21 - Aggiornamento indipendente della Distribuzione settimanale `[UI]`

**Come nutrizionista, voglio aggiornare i valori nutrizionali della Distribuzione settimanale indipendentemente dal Budget, così da poter lavorare sulle singole giornate senza attivare controlli di coerenza ad ogni modifica.**

Criteri principali:

- deve essere disponibile il pulsante **Aggiorna valori medi della Distribuzione settimanale**;

- il pulsante deve ricalcolare macro per pasto, totali per giorno, totali settimanali e relative medie;

- non deve effettuare controlli di coerenza con il Budget;

- non deve modificare Budget, N. volte, Assegnati o Residui.

### US-DIET-22 - Separazione dello stato tra Budget e Distribuzione `[UI]`

**Come sistema, voglio mantenere separato lo stato del Budget dallo stato della Distribuzione settimanale, così da evitare sincronizzazioni implicite e inconsistenze durante i rerun di Streamlit.**

Criteri principali:

- devono essere mantenuti distinti draft Budget, Budget consolidato, grid giornaliere e aggregazioni della Distribuzione;

- una modifica in una sezione non deve provocare automaticamente il ricalcolo dell'altra;

- i valori derivati, come Residui, devono essere ricostruiti dai dati sorgente e non usati come source of truth autonoma.

### US-DIET-23 - Controllo di coerenza bloccante prima del salvataggio `[UI]`

**Come nutrizionista, voglio che il sistema verifichi la coerenza tra Budget e Distribuzione prima di aggiornare o creare un piano, così da evitare il salvataggio di una prescrizione incompleta o incoerente.**

Criteri principali:

- il controllo deve essere eseguito sia per **Aggiorna piano alimentare esistente** sia per **Salva come nuovo piano alimentare**;

- il check deve leggere i dati RAW correnti e non dipendere da aggregazioni precedentemente memorizzate;

- per ogni alimento deve valere `Budget = somma grammature assegnate`;

- il salvataggio deve essere bloccato se Assegnati < Budget, Assegnati > Budget o esistono alimenti fuori Budget;

- in caso di incoerenza deve essere mostrato un warning con il dettaglio degli alimenti coinvolti.

### US-DIET-24 - Caricamento di un piano esistente con ricostruzione Budget `[UI]`

**Come nutrizionista, voglio riaprire un piano alimentare esistente ricostruendo automaticamente Budget e Distribuzione, così da poterlo modificare mantenendo coerenti le quantità già assegnate.**

Criteri principali:

- le grid giorno/pasto devono essere inizializzate dagli item persistiti;

- il Budget deve essere ricostruito aggregando le grammature salvate per alimento;

- Assegnati deve essere inizializzato con le quantità già persistite;

- N. volte deve essere ricostruito contando le occorrenze con grammatura > 0;

- le modifiche successive devono rispettare i cicli separati di aggiornamento Budget e Distribuzione.

---

## Epic D - Workout

### US-WRK-01 - Consultazione workout `[UI]`

**Come nutrizionista, voglio visualizzare i workout associati a un assistito, così da consultare rapidamente le schede di allenamento attive e archiviate.**

### US-WRK-02 - Creazione workout `[UI]`

**Come nutrizionista, voglio creare un nuovo workout associato a un assistito, così da definire una prescrizione di allenamento strutturata.**

### US-WRK-03 - Modifica workout `[UI]`

**Come nutrizionista, voglio modificare un workout esistente mantenendo gli identificativi degli esercizi già storicizzati, così da non perdere le misurazioni associate.**

### US-WRK-04 - Archiviazione e ripristino `[UI]`

**Come nutrizionista, voglio archiviare e successivamente ripristinare un workout, così da rimuoverlo dalla gestione corrente senza cancellarne lo storico.**

### US-WRK-05 - Prescrizione esercizi `[UI]`

**Come nutrizionista, voglio configurare per ogni esercizio serie, ripetizioni, carico, TUT, recupero, RIR e RPE target, così da descrivere in modo strutturato la prescrizione.**

### US-WRK-06 - Blocchi multi-esercizio `[UI]`

**Come nutrizionista, voglio raggruppare esercizi in superset, jump set, tri-set, giant set o circuiti, così da rappresentare anche strutture di allenamento non sequenziali.**

### US-WRK-07 - Tecniche di intensità/densità `[UI]`

**Come nutrizionista, voglio associare tecniche come drop set, rest-pause, myo-reps, cluster, AMRAP o tempo a un esercizio, così da descriverne la modalità di esecuzione oltre ai soli parametri base.**

### US-WRK-08 - Metriche di progressione `[UI]`

**Come nutrizionista, voglio selezionare una o più metriche di progressione per esercizio tra carico, volume, TUT, densità e ripetizioni, così da monitorare l'adattamento secondo il criterio più rilevante.**

### US-WRK-09 - Registrazione performance `[UI]`

**Come nutrizionista, voglio registrare serie e segmenti effettivamente eseguiti durante una seduta, così da confrontare nel tempo prescrizione e performance reale.**

### US-WRK-10 - Tracciamento seduta `[UI/BE]`

**Come nutrizionista, voglio utilizzare lo stesso `execution_id` per gli esercizi svolti nella medesima seduta, così da poter ricondurre le misurazioni a una singola esecuzione del workout.**

### US-WRK-11 - Monitoraggio trend `[UI]`

**Come nutrizionista, voglio visualizzare i trend di carico massimo, volume, TUT, densità e ripetizioni per esercizio, così da valutare quantitativamente la progressione dell'assistito.**

### US-WRK-12 - Storico serie e segmenti `[UI]`

**Come nutrizionista, voglio consultare lo storico dettagliato delle singole serie e dei relativi segmenti, così da analizzare anche tecniche come drop set, rest-pause e cluster.**

---

## Epic E - Catalogo e configurazioni backend

### US-FOOD-01 - CRUD categorie alimentari `[BE]`

**Come amministratore del catalogo, voglio creare, leggere, modificare ed eliminare categorie alimentari, così da classificare gli alimenti disponibili.**

### US-FOOD-02 - Gestione catalogo alimenti `[UI/BE]`

**Come nutrizionista, voglio listare e aggiornare il database dei cibi con la possibilità di aggiungere ed eliminare alimenti, così da mantenere il catalogo utilizzato nei piani alimentari.**

### US-FOOD-03 - Import massivo alimenti `[UI/BE]`

**Come nutrizionista, voglio importare massivamente i miei cibi da CSV o JSON, così da popolare o aggiornare rapidamente il catalogo senza inserimenti manuali ripetitivi.**

### US-FOOD-04 - Catalogo personale per nutrizionista `[BACKLOG - post MVP]`

**Come nutrizionista, voglio distinguere in futuro i cibi associati al mio utente, così da poter disporre di un catalogo personale separato o combinato con quello condiviso.**

### US-MICRO-01 - CRUD riferimenti micronutrienti `[BE]`

**Come amministratore, voglio gestire i riferimenti nutrizionali usati dall'overview micronutrienti, così da aggiornare le soglie senza modificare l'algoritmo di calcolo.**

### US-ANA-01 - Template anamnesi `[BE]`

**Come nutrizionista, voglio disporre di un questionario di anamnesi configurabile e ordinabile, così da standardizzare la raccolta delle informazioni.**

### US-ANA-02 - Salvataggio risposte `[BE]`

**Come nutrizionista, voglio salvare il set corrente di risposte anamnestiche di un assistito, così da mantenere un questionario strutturato associato alla sua scheda.**

---

## Epic F - Correlazione biometria, dieta e workout

### US-BIO-DIET-WRK-01 - Associazione dieta e workout alla misurazione `[BACKLOG]`

**Come nutrizionista, voglio associare ogni misurazione biometrica alla dieta effettivamente seguita e, opzionalmente, al workout svolto, così da interpretare l'evoluzione antropometrica nel contesto delle prescrizioni applicate.**

Criteri previsti:

- una misurazione biometrica deve avere obbligatoriamente una dieta associata;

- il workout associato è opzionale;

- i riferimenti devono puntare a piani già esistenti dell'assistito;

- lo storico deve continuare a essere leggibile anche se dieta o workout vengono successivamente archiviati/modificati;

- l'implementazione richiederà un riallineamento del modello `biometrics` con foreign key esplicite verso `diet_plans` e `workout_plans`.

> Questa user story è progettata ma **non ancora implementata** nella UI, nei service e nel DDL corrente.

---

# Architettura

L'implementazione corrente adotta una separazione semplice tra UI, service e accesso dati.

```text

┌─────────────────────────────────────────────┐

│                 Streamlit UI                │

│ app.py + pages/*.py                         │

└──────────────────────┬──────────────────────┘

                       │ chiamate Python

┌──────────────────────▼──────────────────────┐

│               Service Layer                 │

│ patient_service.py                          │

│ biometrics_service.py                       │

│ food_service.py                             │

│ diet_service.py                             │

│ workout_service.py                          │

└──────────────────────┬──────────────────────┘

                       │ psycopg2

┌──────────────────────▼──────────────────────┐

│                PostgreSQL                   │

└─────────────────────────────────────────────┘

```

Caratteristiche:

- nessun ORM;

- SQL esplicito nei service;

- una nuova connessione DB per operazione tramite `Common.functions.connect()`;

- transazioni esplicite nelle operazioni di scrittura;

- `RealDictCursor` per esporre le righe come dizionari;

- `st.session_state` usato per stato dell'utente, draft del piano e cache UI;

- `st.cache_data` usato nel modulo diete per ridurre il caricamento ripetuto del catalogo alimenti;

- AG Grid + JavaScript custom per l'editor alimentare.

---

# Struttura del progetto

```text

Nutriflow/

│

├── app.py

│   # Entry point Streamlit e inizializzazione stato globale

│

├── pages/

│   ├── 1_pazienti.py

│   ├── 2_biometria.py

│   ├── 3_piani_alimentari.py

│   ├── 4_workout.py

│   └── 5_catalogo_alimenti.py

│

├── Backend/

│   ├── __init__.py

│   ├── main.py                     # Attualmente vuoto

│   │

│   ├── services/

│   │   ├── patient_service.py

│   │   ├── biometrics_service.py

│   │   ├── food_service.py

│   │   ├── diet_service.py

│   │   └── workout_service.py

│   │

│   ├── OneShot/

│   │   └── scripts/

│   │       ├── 00_test.sql

│   │       ├── 01_ddl_schema.sql

│   │       ├── 02_ddl_workout.sql

│   │       ├── execute_scripts.py

│   │       ├── insert_foods.py

│   │       └── lista_alimenti.csv

│   │

│   └── Test/

│       ├── test_patient_service.py

│       └── test_biometrics_service.py

│

├── Common/

│   ├── configuration.py

│   └── functions.py

│

├── .devcontainer/

│   └── devcontainer.json

│

├── requirements.txt

└── README.md

```

---

# Modello dati

## Tabelle utilizzate

| Tabella | Scopo |

|---|---|

| `users` | Identifica il professionista proprietario dei dati; autenticazione non ancora implementata in UI |

| `patients` | Anagrafica, stile di vita, note cliniche e dati usati per il TDEE |

| `biometrics` | Storico time-series di peso, circonferenze, pliche e BF% |

| `food_categories` | Tipologica delle categorie alimentari |

| `foods` | Catalogo alimenti con macro e micronutrienti per 100 g |

| `anamnesis_questions` | Template delle domande di anamnesi |

| `anamnesis_answers` | Risposte dell'assistito alle domande strutturate |

| `diet_plans` | Testata del piano alimentare associato all'assistito |

| `diet_meal_items` | Dettaglio del piano: giorno, pasto, alimento, grammi e macro calcolati |

| `micronutrients_quantities` | Riferimenti usati per l'overview dei micronutrienti |

| `workout_plans` | Testata del piano di allenamento associato all'assistito |

| `workout_exercises` | Prescrizione degli esercizi, blocchi, tecniche e metriche di progressione |

| `workout_measurements` | Storico delle performance per serie/segmento e seduta (`execution_id`) |

## Relazioni principali

```text

users

 ├──< patients

 │     ├──< biometrics

 │     ├──< anamnesis_answers >── anamnesis_questions

 │     ├──< diet_plans

 │     │       └──< diet_meal_items >── foods

 │     └──< workout_plans

 │             └──< workout_exercises

 │                     └──< workout_measurements

 │

 └──< foods

food_categories ──< foods

micronutrients_quantities

   └── collegamento logico/applicativo con le colonne micronutrienti di foods

```

`diet_meal_items` mantiene anche una cache dei macro calcolati al momento del salvataggio del piano (`kcal_calculated`, `prot_calculated`, `carbs_calculated`, `fats_calculated`).

---

# Logiche di calcolo

## Macronutrienti

Per un alimento con valori nutrizionali espressi per 100 g:

```text

ratio = grammi / 100

nutriente_calcolato = nutriente_per_100g × ratio

```

## Micronutrienti

Il piano viene aggregato sull'intera settimana e normalizzato a media giornaliera:

```text

apporto_medio_giornaliero = apporto_totale_settimana / 7

```

Il confronto con la tipologica è effettuato solo se la `reference_basis` configurata è compatibile con la base del dato presente in `foods`.

## Energia da kcal a MJ

Per i riferimenti nutrizionali espressi per MJ:

```text

MJ_giornalieri = kcal_giornaliere / 238.83

```

---

## Metriche workout

Le metriche di progressione vengono derivate dai dati elementari presenti in `workout_measurements` e aggregate per singola seduta (`execution_id`).

### Volume

```text

volume_kg = Σ(reps_completed × load_kg)

```

Il volume non viene persistito come colonna dedicata, evitando ridondanza e possibili incoerenze rispetto ai dati di dettaglio.

### Carico massimo

```text

peak_load_kg = MAX(load_kg)

```

### TUT totale

```text

total_tut_seconds = Σ(tut_seconds)

```

### Densità

La densità viene calcolata solo quando è disponibile una durata attiva maggiore di zero:

```text

density_kg_per_minute = volume_kg / (total_duration_seconds / 60)

```

Se `duration_seconds` non è valorizzato, la densità viene restituita come `NULL`.

### RIR e RPE medi

```text

avg_rir = AVG(rir)

avg_rpe = AVG(rpe)

```

Le metriche vengono calcolate a runtime dal backend e non duplicate nella tabella delle misurazioni.

---

# Installazione ed esecuzione

## Prerequisiti

- Python 3.11 consigliato;

- PostgreSQL raggiungibile dall'ambiente di esecuzione;

- variabile `DATABASE_URL` configurata.

## 1. Creare l'ambiente virtuale

```bash

python -m venv .venv

```

Attivazione Linux/macOS:

```bash

source .venv/bin/activate

```

Attivazione Windows PowerShell:

```powershell

.venv\Scripts\Activate.ps1

```

## 2. Installare le dipendenze

```bash

pip install -r requirements.txt

```

Dipendenze principali:

- `streamlit`;

- `psycopg2-binary`;

- `python-dotenv`;

- `streamlit-aggrid`;

- `reportlab`;

- `pytest`.

## 3. Configurare il database

Creare un file `.env` nella root:

```dotenv

DATABASE_URL=postgresql://USER:PASSWORD@HOST:PORT/DB_NAME

```

> **Attenzione:** lo script `Backend/OneShot/scripts/01_ddl_schema.sql` non è ancora completamente allineato ai service correnti. Per una nuova installazione è necessario applicare le correzioni/migrazioni indicate in [Known gaps e debito tecnico](#known-gaps-e-debito-tecnico) prima di usare il DDL come bootstrap definitivo.

Il modulo Workout dispone di un DDL dedicato:

```text

Backend/OneShot/scripts/02_ddl_workout.sql

```

Lo script crea `workout_plans`, `workout_exercises` e `workout_measurements` con relativi vincoli e indici. Deve essere applicato dopo lo schema base, perché contiene foreign key verso `users` e `patients`.

## 4. Avviare l'app

Dalla root del progetto:

```bash

streamlit run app.py

```

L'app utilizza la navigazione multipagina nativa di Streamlit e rileva automaticamente i file presenti in `pages/`.

---

# Test

La suite attuale contiene test unitari per:

- `patient_service`;

- `biometrics_service`.

Esecuzione:

```bash

pytest Backend/Test -v

```

oppure:

```bash

PYTHONPATH=. pytest Backend/Test -v

```

La copertura non include ancora `diet_service`, `food_service`, `workout_service` e i flussi UI Streamlit/AG Grid.

---

# Known gaps e debito tecnico

Questa sezione documenta incongruenze rilevate confrontando UI, service e DDL. Non descrive funzionalità desiderate, ma punti reali da riallineare nel repository.

## 1. DDL `diet_meal_items` non valido

In `01_ddl_schema.sql` manca una virgola dopo:

```sql

diet_plan_id UUID REFERENCES diet_plans(id) ON DELETE SET NULL

```

prima della colonna `food_id`.

Il DDL, così com'è, non può essere eseguito integralmente su un database nuovo.

## 2. Colonne TDEE mancanti nel DDL

`patient_service.py` legge e aggiorna:

```text

tdee_kcal

tdee_updated_at

```

ma queste colonne non sono presenti nella definizione corrente di `patients` in `01_ddl_schema.sql`.

## 3. Metadati dei riferimenti micronutrienti mancanti nel DDL

`diet_service.py` legge da `micronutrients_quantities` anche:

```text

reference_type

maximum_type

reference_basis

reference_source

```

ma il DDL corrente non definisce tali colonne.

## 4. Naming delle circonferenze non allineato

La UI della biometria invia alcuni campi con naming singolare/abbreviato, ad esempio:

```text

circ_polso_cm

circ_braccio_cm

circ_avambracco_cm

circ_polpaccio_cm

```

mentre il DDL definisce:

```text

circ_polsi_cm

circ_braccia_cm

circ_avambracci_cm

circ_polpacci_cm

```

Questo va normalizzato per evitare errori nelle INSERT su un DB costruito dal DDL corrente.

## 5. Sesso usato nel calcolo BF da verificare

In `pages/2_biometria.py` il valore del sesso viene recuperato da una struttura che mappa etichetta paziente → ID. Il fallback corrente porta di fatto a usare `Maschio` in casi in cui il sesso dell'assistito non viene letto correttamente.

Il calcolo BF dovrebbe utilizzare direttamente `paziente_obj['sesso']`.

## 6. Trend biometrici: `timestamp` vs `insert_date`

La visualizzazione dei grafici di storico cerca una colonna `timestamp`, mentre la tabella `biometrics` e i service utilizzano `insert_date`.

Il dettaglio tabellare funziona, ma i grafici di trend devono essere riallineati a `insert_date`.

## 7. `devcontainer.json` punta a un path non più esistente

La configurazione contiene ancora riferimenti a:

```text

Frontend/app.py

```

mentre l'entry point reale è:

```text

app.py

```

Vanno aggiornati almeno `openFiles` e `postAttachCommand`.

## 8. Script seed alimenti con path hard-coded

`insert_foods.py` utilizza:

```python

CSV_FILE = "ppj/Nutriflow/DB/scripts/lista_alimenti.csv"

```

ma il CSV è presente nel repository in:

```text

Backend/OneShot/scripts/lista_alimenti.csv

```

Il path dovrebbe essere costruito a partire da `Path(__file__).resolve().parent`.

## 9. Commit dello script DDL

`execute_scripts.py` esegue il file SQL ma non effettua esplicitamente `connection.commit()` e non usa la connessione come context manager transazionale.

Su PostgreSQL è opportuno rendere il commit esplicito dopo l'esecuzione corretta degli script.

## 10. Utente applicativo placeholder

Le pagine impostano, in assenza di sessione autenticata:

```text

00000000-0000-0000-0000-000000000000

```

come `user_id`.

Poiché `patients.user_id` è una foreign key verso `users.id`, sul DB deve esistere tale utente oppure deve essere implementata una vera inizializzazione/autenticazione dell'utente.

## 11. Ricette non presenti nell'implementazione corrente

Il vecchio README descriveva ricette e tabelle `recipes` / `recipe_ingredients`, ma nel codice corrente:

- non esiste un `recipe_service`;

- non esiste una pagina ricette;

- il DDL effettua solo il `DROP` delle vecchie tabelle, senza ricrearle.

La capability non va quindi considerata implementata.

---

## 12. Test automatici del modulo Workout mancanti

Il service `workout_service.py` e la pagina `4_workout.py` sono implementati, ma non esiste ancora una suite automatica dedicata a:

- CRUD dei workout;

- aggiornamento con conservazione degli UUID degli esercizi;

- archiviazione/ripristino;

- gestione dei blocchi;

- validazione delle tecniche;

- inserimento di serie/segmenti;

- aggregazione di volume, TUT e densità.

## 13. Biometria non ancora correlata a dieta e workout

La user story che prevede una dieta obbligatoria e un workout opzionale per ogni misurazione biometrica è ancora in backlog.

Il modello corrente non contiene ancora foreign key esplicite da `biometrics` verso `diet_plans` e `workout_plans`; la modifica dovrà inoltre preservare la leggibilità storica delle associazioni.

## 14. Budget settimanale non persistito separatamente

Il Budget alimentare settimanale è attualmente uno stato di lavoro gestito nell'editor Streamlit e non dispone di una tabella/entità DB dedicata.

Quando viene riaperto un piano esistente, il Budget viene quindi ricostruito a partire da `diet_meal_items` aggregando le grammature realmente salvate. Di conseguenza, un eventuale target teorico non completamente allocato non può essere recuperato dopo la chiusura della sessione.

Se sarà necessario conservare anche il Budget originario come informazione indipendente dalla Distribuzione, il modello dati dovrà essere esteso con una persistenza dedicata del target settimanale per alimento.

---

## Evoluzioni consigliate

Le priorità tecniche più immediate sono:

1. riallineare DDL e codice con una migration versionata;

2. implementare l'associazione `biometrics → diet_plan` obbligatoria e `biometrics → workout_plan` opzionale;

3. correggere naming e trend del modulo biometrico;

4. rimuovere il `user_id` placeholder introducendo un'identità applicativa coerente;

5. aggiornare devcontainer e script di bootstrap;

6. aggiungere test per `diet_service`, micronutrienti e `workout_service`;

7. estendere, se necessario, la UI del catalogo alla manutenzione delle categorie e dei riferimenti micronutrienti;

8. introdurre post-MVP la separazione/ownership del catalogo alimenti per nutrizionista;

9. collegare alla UI il questionario di anamnesi strutturato già supportato dal backend.

10. valutare una persistenza DB dedicata del Budget settimanale quando sarà necessario conservare target non ancora completamente allocati dopo la chiusura della sessione.

---

## Stack tecnologico

- **Python**

- **Streamlit**

- **streamlit-aggrid / AG Grid**

- **PostgreSQL**

- **psycopg2**

- **Pandas**

- **ReportLab**

- **PyTest**

---

**Nutriflow** copre attualmente quattro aree operative principali: **gestione assistito, biometria, pianificazione alimentare e workout**. Il modulo Workout aggiunge prescrizione strutturata, blocchi e tecniche di allenamento, tracking per serie/segmento e analisi della progressione; la prossima evoluzione prevista è correlare ogni rilevazione biometrica alla dieta seguita e, opzionalmente, al workout svolto.