# Nutriflow 🍏

**Nutriflow** è un applicativo web ad uso personale progettato per la gestione completa e professionale di uno studio nutrizionale. Il software copre l'intero ciclo operativo: dalla gestione di un database proprietario di alimenti e ricette all'anamnesi iniziale, passando per il tracciamento biometrico avanzato con calcolo della massa grassa, la composizione in tempo reale di piani alimentari personalizzati e l'esportazione automatica in PDF con relativa lista della spesa aggregata.

---

## 🎯 Obiettivo del Progetto

Fornire uno strumento software altamente performante, modulare e orientato ai dati per un nutrizionista, basato su un'architettura pulita (**Separation of Concerns** e **Domain-Driven Design**) e sviluppato con uno stack agile ed efficiente (Python, Streamlit, PostgreSQL/Supabase).

---

## 📁 Struttura del Progetto

L'alberatura del progetto separa rigorosamente la logica di business, la persistenza dei dati e l'interfaccia grafica:

```text
Nutriflow/
│
├── Common/
│   ├── __init__.py
│   ├── configuration.py       # Gestione delle configurazioni e variabili d'ambiente (.env)
│   └── functions.py           # Utility condivise (es. gestione connessione al database)
│
├── Backend/
│   ├── __init__.py
│   ├── database.py            # Setup e gestione del connection pool
│   ├── services/              # Logica di business suddivisa per domini
│   │   ├── __init__.py
│   │   ├── auth_service.py    # Gestione utente e permessi
│   │   ├── food_service.py    # Catalogo alimenti, categorie e ricette compound
│   │   ├── patient_service.py # Anagrafica pazienti e anamnesi
│   │   ├── biometrics_service.py # Misure antropometriche e algoritmi BF
│   │   ├── diet_service.py    # Motore di composizione e storico diete
│   │   └── export_service.py  # Generazione PDF e aggregatore lista della spesa
│   └── Tests/                 # Suite di unit test (PyTest)
│       ├── __init__.py
│       └── test_patient_service.py
│
├── Frontend/
│   ├── __init__.py
│   ├── app.py                 # Entry point principale di Streamlit e gestione stato globale
│   ├── components/            # Elementi UI riutilizzabili (es. sidebar, card)
│   └── pages/                 # Pagine/Viste multi-step dell'applicazione
│       ├── 1_👥_Pazienti.py
│       └── 2_📈_Biometria.py
│
├── .env                       # Variabili d'ambiente (Credenziali DB, API Keys)
├── requirements.txt           # Dipendenze Python
└── README.md                  # Documentazione del progetto
```

## User Stories & Requisiti Funzionali
### Database Alimenti & Ricette Personali:
Il nutrizionista gestisce un database proprietario di alimenti completi di macronutrienti, indice glicemico e micronutrienti (vitamine e minerali). È possibile aggiungere, modificare, eliminare voci e creare ricette multi-ingrediente componendo preparazioni complesse.

### Anagrafica & Anamnesi Pazienti:
Gestione completa delle schede cliente (dati biometrici, preferenze, patologie, stile di vita e attitudine professionale) supportata da questionari anamnestici preimpostati e personalizzabili.

### Composizione Dietetica in Tempo Real-Time:
Creazione di piani alimentari flessibili con numero di pasti definibile a monte, filtri preliminari di esclusione per categorie di cibi e ricalcolo istantaneo a schermo di tutti i macro e micronutrienti.

### Tracciamento Biometrico & Storico:
Registrazione periodica delle misurazioni antropometriche (peso, circonferenze e pliche cutanee) con calcolo della percentuale di massa grassa (BF%) tramite diversi algoritmi selezionabili (es. Jackson-Pollock, Durnin-Womersley) e visualizzazione dei trend temporali tramite grafici interattivi.

### Storico Diete & Deep Dive:
Associazione delle diete ai pazienti e consultazione dello storico dei piani alimentari assegnati, con analisi dei macro attuali e passati.

### Esportazione PDF & Lista Spesa:
Generazione di documenti PDF formattati professionalmente per il cliente, accompagnati da una lista della spesa specifica e aggregata per categoria merceologica.

## Data Model (Schema Relazionale)
Il database relazionale si articola sui seguenti domini e tabelle principali:

users: Memorizza i dati del professionista e il ruolo.

patients: Anagrafica dei clienti collegata al nutrizionista (user_id), comprensiva di dati anagrafici, stile di vita e patologie.

biometrics: Storico time-series delle misurazioni antropometriche (peso, circonferenze multiple e pliche cutanee), con tracciamento dell'algoritmo BF impiegato.

food_categories & foods: Catalogo degli alimenti suddiviso per categorie (utile per i filtri di esclusione), contenente il profilo nutrizionale completo (macro, micro, sali minerali e acidi grassi).

recipes & recipe_ingredients: Gestione delle preparazioni complesse tramite relazione molti-a-molti tra ricette e singoli alimenti di base.

anamnesis_questions & anamnesis_answers: Template delle domande di anamnesi configurate dal professionista e relative risposte salvate per ciascun paziente.

diet_plans, diet_meals : Architettura gerarchica per la definizione dei piani alimentari.

## Requisiti di Installazione ed Esecuzione
Clona o apri il progetto nella root Nutriflow/.

Installa le dipendenze Python:

```Bash
pip install -r requirements.txt
Configura il file .env nella root inserendo la stringa di connessione al database (DATABASE_URL).
```
Esegui i test unitari:

```Bash
pytest Backend/Tests/ -v
Avvia l'interfaccia grafica (Streamlit):
```
Avvia il frontend:
```Bash
PYTHONPATH=. streamlit run Frontend/app.py
```