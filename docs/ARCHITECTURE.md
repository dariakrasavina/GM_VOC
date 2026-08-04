# GM VOC POC — Architecture (Mermaid)

Databricks replication of Qualtrics XM Discover, with three tracks: a
deterministic **rule engine**, a **lightweight AI-powered solution** built on
Databricks AI functions (`ai_classify`, `ai_analyze_sentiment`, `ai_query`
embeddings + KMeans, `ai_gen`), and a **trained transformer sentiment model**
(fine-tuned DistilBERT, 5-class, MLflow-tracked + VADER baseline).

These diagrams render natively on GitHub and in any Mermaid viewer
(e.g. https://mermaid.live). An HTML version is in `architecture_diagram.html`.

---

## 1. Component map & build-time flow

```mermaid
flowchart TB
    subgraph SRC["Source of truth (client rules)"]
        XLSX["Qualtrics_Parent_and_Leaf_Nodes.xlsx<br/>(4 topic nodes + global filter)"]
    end

    subgraph GEN["Rule config generation (one-time / on change)"]
        BUILD["build_rules_config.py"]
        RULES["rules.json<br/>(hierarchy + lane rules as data)"]
    end

    subgraph ENGINE["Engine (pure Python, no pyspark)"]
        RE["rule_engine.py<br/>parser + evaluator:<br/>OR / AND / NOT, phrases,<br/>wildcards, fuzzy, proximity, attr:"]
        TG["tagger.py<br/>global scope filter + 4 topic nodes"]
        TEST["test_rule_engine.py<br/>33 unit tests"]
    end

    subgraph LOCAL["Local validation (stdlib only)"]
        FIX["make_demo_fixture.py<br/>schema-identical demo CSVs"]
        RL["run_local.py<br/>load - join - filter - tag"]
        OUT["outputs/<br/>tagged_sentences.csv,<br/>topic_frequencies.csv,<br/>representative_verbatims.json,<br/>run_summary.json"]
        DASH["build_dashboard.py -> dashboard.html"]
    end

    subgraph PROD["Databricks — Track 1: rule engine"]
        JOB["voc_topic_model_job.py<br/>PySpark + pandas_udf"]
        NB["run_job_notebook.py<br/>(entrypoint notebook)"]
        YML["databricks.yml<br/>(Asset Bundle)"]
    end

    subgraph ML["Databricks — Track 2: Lightweight AI-powered solution (AI functions)"]
        AICLS["ai_classify_job.py<br/>ai_classify + ai_analyze_sentiment"]
        DISC["topic_discovery_job.py<br/>ai_query embeddings + KMeans + ai_gen"]
        CMP["compare_approaches_job.py<br/>rules vs AI agreement"]
    end

    XLSX --> BUILD --> RULES
    RULES --> TG
    RE --> TG
    RE --> TEST
    TG --> RL
    TG --> JOB
    FIX --> RL
    RL --> OUT --> DASH
    YML --> NB --> JOB
    RULES -. shipped with job .-> JOB
    RULES -. topic definitions .-> AICLS
    YML --> AICLS
    YML --> DISC
    JOB --> CMP
    AICLS --> CMP
```

---

## 2. Runtime execution order — rule track (`voc_topic_model_job`)

```mermaid
sequenceDiagram
    participant U as You (CLI)
    participant B as Asset Bundle
    participant W as Databricks Workspace
    participant N as run_job_notebook.py
    participant J as voc_topic_model_job.run()
    participant UDF as pandas_udf (tagger + rule_engine)
    participant UC as Unity Catalog (Delta)

    U->>B: databricks bundle deploy
    B->>W: upload topic_model/ + create job
    U->>B: databricks bundle run
    B->>W: trigger job (serverless)
    W->>N: run entrypoint notebook
    N->>N: add folder to sys.path
    N->>J: import job and call run()
    J->>J: get_params() from widgets
    J->>UC: read sentence_table + metadata_table
    J->>J: join on natural_id, filter document_date range
    J->>UDF: apply tag_udf over struct(words + attrs)
    UDF->>UDF: in_scope() global filter -> tag() 4 nodes
    UDF-->>J: JSON {in_scope, topics:{id:[terms]}}
    J->>J: explode to per-topic columns + __terms
    J->>UC: write voc_topic_tags (per sentence)
    J->>UC: write voc_topic_frequencies (aggregates)
    J-->>U: done
```

---

## 3. Runtime execution order — lightweight AI-powered solution (`voc_ai_pipeline_job`)

```mermaid
sequenceDiagram
    participant U as You (CLI)
    participant W as Databricks (serverless, DBR 18.2+)
    participant AI as AI functions (LLM / embeddings)
    participant ML as Spark MLlib KMeans
    participant UC as Unity Catalog (Delta)

    U->>W: databricks bundle run voc_ai_pipeline_job

    rect rgb(235,244,255)
    Note over W,UC: Task A — ai_classify_job
    W->>UC: read + scope verbatims (EN / audio / customer)
    W->>AI: ai_classify(words, topic definitions from rules.json)
    W->>AI: ai_analyze_sentiment(words)
    AI-->>W: topic label + confidence + sentiment
    W->>UC: write voc_ai_topic_tags
    end

    rect rgb(238,247,238)
    Note over W,UC: Task B — topic_discovery_job
    W->>AI: ai_query('databricks-gte-large-en', words) embeddings
    AI-->>W: dense vectors
    W->>ML: KMeans cluster vectors into themes
    ML-->>W: theme_id per sentence
    W->>AI: ai_gen(examples) name + summarize each theme
    W->>UC: write voc_discovered_themes + voc_theme_assignments
    end

    rect rgb(252,244,235)
    Note over W,UC: Task C — compare_approaches_job (after A + rule job)
    W->>UC: read voc_topic_tags (rules) + voc_ai_topic_tags (AI)
    W->>W: agreement, rule-only, AI-only, precision/recall/F1
    W->>UC: write voc_approach_comparison
    end
    W-->>U: done
```

---

## 4. Runtime execution order — trained sentiment model (`voc_sentiment_model_job`)

```mermaid
sequenceDiagram
    participant U as You (CLI)
    participant W as Databricks (DBR ML + GPU)
    participant AI as LLM (ai_query, weak labeling)
    participant HF as HuggingFace Trainer (DistilBERT)
    participant ML as MLflow / Unity Catalog
    participant UC as Unity Catalog (Delta)

    U->>W: databricks bundle run voc_sentiment_model_job

    rect rgb(235,244,255)
    Note over W,UC: Task A — train_sentiment_model
    W->>UC: scope + sample verbatims
    W->>AI: ai_query weak-label 5-class sentiment
    AI-->>W: Very Neg .. Very Pos labels
    W->>UC: write voc_sentiment_weak_labels (audit)
    W->>HF: fine-tune DistilBERT on weak labels
    HF-->>W: model + accuracy / macro-F1
    W->>ML: log params/metrics/model, register in UC
    end

    rect rgb(238,247,238)
    Note over W,UC: Task B — score_sentiment
    W->>ML: load registered transformer
    W->>W: score verbatims (transformer) + VADER lexicon baseline
    W->>UC: write voc_sentiment_scored (both methods per sentence)
    end
    W-->>U: done
```

---

## Reading it

- **Build time:** the client Excel drives `rules.json`; the engine
  (`rule_engine.py` + `tagger.py`) is validated by tests and the local runner
  before it ever reaches Spark. The same `rules.json` topic definitions steer
  the AI classifier, so both tracks classify against identical topics.
- **Track 1 (rules):** the same engine runs inside the Spark `pandas_udf`, so
  local and production results are identical — deterministic, explainable, the
  XM Discover control replica.
- **Track 2 (lightweight AI-powered solution):** Databricks AI functions classify by meaning
  (`ai_classify`), add sentiment (`ai_analyze_sentiment`), and discover emergent
  themes (embeddings + KMeans + `ai_gen`) — no model to train or maintain. The
  compare job regresses AI against the rule control. Requires serverless compute
  + DBR 18.2+; AI calls are pay-per-token.
- **Track 3 (trained transformer sentiment):** weak-label 5-class sentiment with
  an LLM, fine-tune DistilBERT, and register it in MLflow / Unity Catalog — a
  model GM owns and serves at zero token cost. A VADER lexicon baseline mirrors
  the pre-transformer era. Requires DBR ML (GPU to train). See
  `SENTIMENT_MODEL.md`. This is the Qualtrics-faithful *trained* ML track — a
  single transformer, not an ensemble.
